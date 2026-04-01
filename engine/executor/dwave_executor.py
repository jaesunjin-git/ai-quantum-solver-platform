# engine/executor/dwave_executor.py
from __future__ import annotations

import time
import logging
from typing import Dict, Any

from .base import BaseExecutor, ExecuteResult

logger = logging.getLogger(__name__)


class DWaveExecutor(BaseExecutor):

    def __init__(self):
        self.token = None

    def _get_token(self) -> str:
        if self.token:
            return self.token
        import os
        self.token = os.getenv("DWAVE_API_TOKEN", "")
        if not self.token:
            try:
                from core.config import settings
                self.token = settings.DWAVE_API_TOKEN or ""
            except Exception:
                pass
        return self.token

    def execute(self, compile_result, **kwargs) -> ExecuteResult:
        solver_type = compile_result.solver_type
        time_limit = kwargs.get("time_limit_sec", 300)

        if solver_type == "cqm":
            return self._execute_cqm(compile_result, time_limit)
        elif solver_type == "bqm":
            return self._execute_bqm(compile_result, time_limit)
        elif solver_type == "nl":
            return self._execute_nl(compile_result, time_limit)
        else:
            return ExecuteResult(success=False, error=f"Unknown solver_type: {solver_type}")

    def _execute_cqm(self, compile_result, time_limit) -> ExecuteResult:
        token = self._get_token()
        if not token:
            return ExecuteResult(success=False, error="DWAVE_API_TOKEN not configured")

        try:
            from dwave.system import LeapHybridCQMSampler

            cqm = compile_result.solver_model
            var_map = compile_result.variable_map

            sampler = LeapHybridCQMSampler(token=token)

            logger.info(f"CQM: submitting to D-Wave (time_limit={time_limit}s)")
            start = time.time()
            sampleset = sampler.sample_cqm(cqm, time_limit=time_limit)

            # NOTE: sample_cqm()은 future를 즉시 반환.
            # filter()/first 접근 시 blocking resolve → 여기서 wall_time 측정.
            feasible = sampleset.filter(lambda s: s.is_feasible)
            if len(feasible) > 0:
                best = feasible.first
                status = "FEASIBLE"
                success = True
            elif len(sampleset) > 0:
                best = sampleset.first
                status = "INFEASIBLE_BEST"
                success = True
            else:
                elapsed = time.time() - start
                return ExecuteResult(
                    success=False,
                    solver_type="cqm",
                    status="NO_SOLUTION",
                    execution_time_sec=round(elapsed, 3),
                )
            elapsed = time.time() - start  # resolve 완료 후 측정

            # Extract solution
            solution = {}
            for vid, v in var_map.items():
                if isinstance(v, dict):
                    solution[vid] = {}
                    for key, var in v.items():
                        var_name = var.variables[0] if hasattr(var, 'variables') else str(var)
                        val = best.sample.get(var_name, 0)
                        if val != 0:
                            solution[vid][str(key)] = val
                else:
                    var_name = v.variables[0] if hasattr(v, 'variables') else str(v)
                    solution[vid] = best.sample.get(var_name, 0)

            obj_val = float(best.energy) if hasattr(best, 'energy') and best.energy is not None else None

            # D-Wave 서버 측 실행 시간 (과금 기준)
            # Hybrid solver: sampleset.info["charge_time"] (초 단위, top-level)
            # QPU solver: sampleset.info["timing"]["charge_time"] (μs, nested)
            dwave_timing = sampleset.info.get("timing", {})
            charge_time_s = sampleset.info.get("charge_time", 0)
            if not charge_time_s:
                # QPU fallback: timing dict 내 μs 단위
                charge_time_us = dwave_timing.get("charge_time", 0)
                charge_time_s = charge_time_us / 1_000_000 if charge_time_us else 0

            logger.info(
                f"CQM: status={status}, energy={obj_val}, "
                f"wall_time={elapsed:.1f}s, charge_time={charge_time_s:.1f}s, "
                f"feasible={len(feasible)}/{len(sampleset)}"
            )

            # ── INFEASIBLE_BEST → 경량 repair (최대 2 round) ──
            repaired = False
            if status == "INFEASIBLE_BEST":
                solution, repaired = self._repair_ir_coverage(
                    solution, var_map, max_rounds=2
                )
                if repaired:
                    # repair 후 재검증
                    remaining = self._check_ir_coverage(solution, var_map)
                    if not remaining:
                        status = "FEASIBLE_REPAIRED"
                        success = True
                        logger.info("CQM IR repair: success → FEASIBLE_REPAIRED")
                    else:
                        status = "INFEASIBLE_POST"
                        logger.warning(
                            f"CQM IR repair: {len(remaining)} violations remain "
                            f"→ INFEASIBLE_POST"
                        )

            # INFEASIBLE 진단 정보 생성
            infeasibility_info = None
            if status in ("INFEASIBLE_BEST", "INFEASIBLE_POST"):
                metadata = compile_result.metadata or {}
                constraint_info = metadata.get("constraint_info", [])
                hard_names = [c["name"] for c in constraint_info
                              if c.get("category") == "hard" and c.get("count", 0) > 0]
                infeasibility_info = {
                    "summary": {
                        "total_samples": len(sampleset),
                        "feasible_samples": 0,
                        "best_energy": obj_val,
                    },
                    "conflict_hints": [
                        {
                            "type": "all_samples_infeasible",
                            "message": (
                                f"D-Wave CQM이 {len(sampleset)}개 샘플을 생성했으나 "
                                f"실현 가능한 해가 없습니다. 하드 제약 간 충돌이 있을 수 있습니다."
                            ),
                            "constraints": hard_names[:10],
                        }
                    ],
                }

            return ExecuteResult(
                success=success,
                solver_type="cqm",
                status="FEASIBLE" if status == "FEASIBLE_REPAIRED" else status,
                objective_value=obj_val,
                solution=solution,
                execution_time_sec=round(elapsed, 3),
                solver_info={
                    "total_samples": len(sampleset),
                    "feasible_samples": len(feasible),
                    "qpu_access_time": sampleset.info.get("qpu_access_time", 0),
                    "charge_time": sampleset.info.get("charge_time", 0),
                    "timing": dwave_timing,
                    "repaired": repaired,
                },
                raw_response=sampleset,
                infeasibility_info=infeasibility_info,
            )

        except ImportError:
            return ExecuteResult(success=False, error="dwave-system not installed. Run: pip install dwave-system")
        except Exception as e:
            logger.error(f"CQM execution failed: {e}", exc_info=True)
            return ExecuteResult(success=False, solver_type="cqm", error=str(e))

    # ── IR coverage repair ────────────────────────────────────

    @staticmethod
    def _parse_tuple_key(key_str: str):
        """solution key 문자열 "(i, j)" → tuple 파싱."""
        import ast
        try:
            return ast.literal_eval(key_str)
        except Exception:
            return None

    @staticmethod
    def _check_ir_coverage(solution, var_map):
        """IR 경로 coverage 검증. 위반 trip 목록 반환 (빈 list = 정상)."""
        from collections import defaultdict

        x_vars = var_map.get("x", {})
        if not x_vars:
            return []

        all_trips = set()
        for key in x_vars:
            if isinstance(key, tuple) and len(key) >= 2:
                all_trips.add(key[0])

        x_sol = solution.get("x", {})
        coverage = defaultdict(int)
        for key_str, val in x_sol.items():
            if float(val) < 0.5:
                continue
            parsed = DWaveExecutor._parse_tuple_key(key_str)
            if parsed and len(parsed) >= 2:
                coverage[parsed[0]] += 1

        violations = []
        for tid in all_trips:
            cnt = coverage.get(tid, 0)
            if cnt != 1:
                violations.append((tid, cnt))
        return violations

    @staticmethod
    def _repair_ir_coverage(solution, var_map, max_rounds=2):
        """IR(assignment) 경로 coverage repair (경량 1-2 round).

        x[i,j]=1: trip i를 crew j에 배정. coverage 조건: Σ_j x[i,j] == 1.
        - uncovered (count=0): load 가장 적은 crew에 배정
        - duplicate (count>1): 하나만 남기고 제거
        """
        from collections import defaultdict

        x_vars = var_map.get("x", {})
        if not x_vars:
            return solution, False

        # var_map에서 trip → 가능한 crew 매핑 구축
        all_trips = set()
        trip_to_crew_keys = defaultdict(list)  # trip → [(key_tuple, key_str)]
        for key in x_vars:
            if isinstance(key, tuple) and len(key) >= 2:
                trip_id = key[0]
                all_trips.add(trip_id)
                trip_to_crew_keys[trip_id].append((key, str(key)))

        if not all_trips:
            return solution, False

        x_sol = solution.get("x", {})
        y_sol = solution.get("y", {})
        repaired = False

        for round_num in range(max_rounds):
            # 현재 trip별 활성 배정 파악
            trip_active = defaultdict(list)   # trip → [(key_str, crew_id)]
            crew_load = defaultdict(int)

            for key_str, val in x_sol.items():
                if float(val) < 0.5:
                    continue
                parsed = DWaveExecutor._parse_tuple_key(key_str)
                if parsed and len(parsed) >= 2:
                    trip_id, crew_id = parsed[0], parsed[-1]
                    trip_active[trip_id].append((key_str, crew_id))
                    crew_load[crew_id] += 1

            uncovered = [t for t in all_trips if not trip_active.get(t)]
            duplicated = {t: assigns for t, assigns in trip_active.items()
                          if len(assigns) > 1}

            if not uncovered and not duplicated:
                break

            logger.info(
                f"IR repair round {round_num + 1}: "
                f"uncovered={len(uncovered)}, duplicate={len(duplicated)}"
            )

            # Case A: uncovered → load 가장 적은 crew에 배정
            for trip_id in uncovered:
                candidates = trip_to_crew_keys.get(trip_id, [])
                if candidates:
                    best_key, best_str = min(
                        candidates, key=lambda c: crew_load.get(c[0][-1], 0)
                    )
                    x_sol[best_str] = 1.0
                    crew_id = best_key[-1]
                    crew_load[crew_id] += 1
                    y_sol[str((crew_id,))] = 1.0
                    repaired = True

            # Case B: duplicate → load 가장 적은 crew 것만 유지, 나머지 제거
            for trip_id, assigns in duplicated.items():
                assigns_sorted = sorted(assigns, key=lambda a: crew_load.get(a[1], 0))
                # 첫 번째(load 최소) 유지, 나머지 제거
                for key_str, crew_id in assigns_sorted[1:]:
                    if key_str in x_sol:
                        del x_sol[key_str]
                    crew_load[crew_id] = max(0, crew_load.get(crew_id, 0) - 1)
                    repaired = True

        solution["x"] = x_sol
        solution["y"] = y_sol
        return solution, repaired

    def _execute_bqm(self, compile_result, time_limit) -> ExecuteResult:
        token = self._get_token()
        if not token:
            return ExecuteResult(success=False, error="DWAVE_API_TOKEN not configured")

        try:
            from dwave.system import LeapHybridSampler

            bqm = compile_result.solver_model
            var_map = compile_result.variable_map

            sampler = LeapHybridSampler(token=token)

            logger.info(f"BQM: submitting to D-Wave (time_limit={time_limit}s)")
            start = time.time()
            sampleset = sampler.sample(bqm, time_limit=time_limit)

            # NOTE: sample()은 future 반환 → 결과 접근 시 blocking resolve
            if len(sampleset) == 0:
                elapsed = time.time() - start
                return ExecuteResult(
                    success=False,
                    solver_type="bqm",
                    status="NO_SOLUTION",
                    execution_time_sec=round(elapsed, 3),
                )

            best = sampleset.first
            elapsed = time.time() - start  # resolve 완료 후 측정

            # Extract solution
            solution = {}
            for vid, v in var_map.items():
                if isinstance(v, dict):
                    solution[vid] = {}
                    for key, name in v.items():
                        val = best.sample.get(name, 0)
                        if val != 0:
                            solution[vid][str(key)] = val
                elif isinstance(v, str):
                    solution[vid] = best.sample.get(v, 0)

            obj_val = best.energy

            logger.info(f"BQM: energy={obj_val}, wall_time={elapsed:.1f}s, samples={len(sampleset)}")

            return ExecuteResult(
                success=obj_val is not None,
                solver_type="bqm",
                status="FEASIBLE",
                objective_value=obj_val,
                solution=solution,
                execution_time_sec=round(elapsed, 3),
                solver_info={
                    "total_samples": len(sampleset),
                    "qpu_access_time": sampleset.info.get("qpu_access_time", 0),
                    "charge_time": sampleset.info.get("charge_time", 0),
                    "timing": sampleset.info.get("timing", {}),
                },
                raw_response=sampleset,
            )

        except ImportError:
            return ExecuteResult(success=False, error="dwave-system not installed. Run: pip install dwave-system")
        except Exception as e:
            logger.error(f"BQM execution failed: {e}", exc_info=True)
            return ExecuteResult(success=False, solver_type="bqm", error=str(e))

    def _execute_nl(self, compile_result, time_limit) -> ExecuteResult:
        """NL/Stride 모델 실행 (LeapHybridNLSampler)"""
        token = self._get_token()
        if not token:
            return ExecuteResult(success=False, error="DWAVE_API_TOKEN not configured")

        try:
            from dwave.system import LeapHybridNLSampler

            nl_model = compile_result.solver_model
            var_map = compile_result.variable_map

            sampler = LeapHybridNLSampler(token=token)

            logger.info(f"NL: submitting to D-Wave Stride (time_limit={time_limit}s)")
            start = time.time()
            sampler.sample(nl_model, time_limit=time_limit)
            elapsed = time.time() - start

            # NL 모델에서 솔루션 추출
            solution = {}
            obj_val = None
            num_states = 0
            is_feasible = False

            with nl_model.lock():
                num_states = nl_model.states.size()

                # best state 선택: feasible 우선, 없으면 best-effort (state 0)
                best_idx = None
                is_feasible = False

                if num_states > 0:
                    # feasible state 검색
                    for si in range(num_states):
                        try:
                            if nl_model.feasible(si):
                                best_idx = si
                                is_feasible = True
                                break
                        except Exception:
                            pass

                    # feasible 없으면 state 0 (best-effort)
                    if best_idx is None:
                        best_idx = 0

                if best_idx is not None:
                    if is_feasible:
                        status = "FEASIBLE"
                        success = True
                    else:
                        status = "INFEASIBLE_BEST"
                        success = True  # 해는 있으므로 결과 전달

                    # 목적함수 값 추출
                    try:
                        obj_val = float(nl_model.objective.state(best_idx))
                    except Exception:
                        pass

                    # 변수 값 추출 (v2: NL 심볼 직접 저장 방식)
                    for vid, var_data in var_map.items():
                        if isinstance(var_data, dict):
                            solution[vid] = {}
                            for key, entry in var_data.items():
                                try:
                                    if isinstance(entry, tuple):
                                        arr, idx = entry
                                        val = float(arr.state(best_idx, idx))
                                    else:
                                        val = float(entry.state(best_idx))
                                    if val != 0:
                                        solution[vid][str(key)] = val
                                except Exception:
                                    pass
                        else:
                            try:
                                if isinstance(var_data, tuple):
                                    val = float(var_data[0].state(best_idx, var_data[1]))
                                else:
                                    val = float(var_data.state(best_idx))
                                solution[vid] = val
                            except Exception:
                                solution[vid] = 0
                else:
                    status = "INFEASIBLE"
                    success = False

            logger.info(
                f"NL: status={status}, obj={obj_val}, time={elapsed:.2f}s, "
                f"states={num_states}, feasible={is_feasible}"
            )

            return ExecuteResult(
                success=success,
                solver_type="nl",
                status=status,
                objective_value=obj_val,
                solution=solution,
                execution_time_sec=round(elapsed, 3),
                solver_info={
                    "num_feasible": 1 if success else 0,
                    "num_states": num_states,
                },
                raw_response=nl_model,
            )

        except ImportError:
            return ExecuteResult(
                success=False,
                error="dwave-system not installed. Run: pip install dwave-system",
            )
        except Exception as e:
            logger.error(f"NL execution failed: {e}", exc_info=True)
            return ExecuteResult(success=False, solver_type="nl", error=str(e))
