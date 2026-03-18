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
            elapsed = time.time() - start

            # Filter feasible solutions
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
                return ExecuteResult(
                    success=False,
                    solver_type="cqm",
                    status="NO_SOLUTION",
                    execution_time_sec=round(elapsed, 3),
                )

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

            logger.info(f"CQM: status={status}, energy={obj_val}, time={elapsed:.2f}s, feasible={len(feasible)}/{len(sampleset)}")

            # INFEASIBLE_BEST 진단 정보 생성
            infeasibility_info = None
            if status == "INFEASIBLE_BEST":
                metadata = compile_result.metadata or {}
                constraint_info = metadata.get("constraint_info", [])
                hard_names = [c["name"] for c in constraint_info if c.get("category") == "hard" and c.get("count", 0) > 0]
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
                status=status,
                objective_value=obj_val,
                solution=solution,
                execution_time_sec=round(elapsed, 3),
                solver_info={
                    "total_samples": len(sampleset),
                    "feasible_samples": len(feasible),
                    "qpu_access_time": sampleset.info.get("qpu_access_time", 0),
                    "charge_time": sampleset.info.get("charge_time", 0),
                    "timing": sampleset.info.get("timing", {}),
                },
                raw_response=sampleset,
                infeasibility_info=infeasibility_info,
            )

        except ImportError:
            return ExecuteResult(success=False, error="dwave-system not installed. Run: pip install dwave-system")
        except Exception as e:
            logger.error(f"CQM execution failed: {e}", exc_info=True)
            return ExecuteResult(success=False, solver_type="cqm", error=str(e))

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
            elapsed = time.time() - start

            if len(sampleset) == 0:
                return ExecuteResult(
                    success=False,
                    solver_type="bqm",
                    status="NO_SOLUTION",
                    execution_time_sec=round(elapsed, 3),
                )

            best = sampleset.first

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

            logger.info(f"BQM: energy={obj_val}, time={elapsed:.2f}s, samples={len(sampleset)}")

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
