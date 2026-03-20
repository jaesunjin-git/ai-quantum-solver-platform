# engine/executor/ortools_executor.py
from __future__ import annotations

import time
import logging
from typing import Dict, Any

from .base import BaseExecutor, ExecuteResult

logger = logging.getLogger(__name__)


class ORToolsExecutor(BaseExecutor):

    def execute(self, compile_result, **kwargs) -> ExecuteResult:
        solver_type = compile_result.solver_type
        time_limit = kwargs.get("time_limit_sec", 120)

        if solver_type == "ortools_cp":
            return self._execute_cp_sat(compile_result, time_limit)
        elif solver_type == "ortools_lp":
            return self._execute_lp(compile_result, time_limit)
        else:
            return ExecuteResult(success=False, error=f"Unknown solver_type: {solver_type}")

    def _execute_cp_sat(self, compile_result, time_limit) -> ExecuteResult:
        from ortools.sat.python import cp_model

        model = compile_result.solver_model
        var_map = compile_result.variable_map

        import os
        num_workers = int(os.environ.get("CPSAT_NUM_WORKERS", min(os.cpu_count() or 4, 8)))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = num_workers

        logger.info(f"CP-SAT: solving with time_limit={time_limit}s, workers={num_workers}")
        start = time.time()
        try:
            status = solver.solve(model)
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"CP-SAT solve() crashed: {e}", exc_info=True)
            return ExecuteResult(
                success=False, solver_type="ortools_cp", status="ERROR",
                error=f"Solver crashed: {e}",
                execution_time_sec=round(elapsed, 3),
            )
        elapsed = time.time() - start

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "ERROR",
            cp_model.UNKNOWN: "TIMEOUT",
        }
        status_str = status_map.get(status, "UNKNOWN")
        success = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

        solution = {}
        obj_val = None
        if success:
            obj_val = solver.objective_value
            for vid, v in var_map.items():
                if isinstance(v, dict):
                    solution[vid] = {}
                    for key, var in v.items():
                        val = solver.value(var)
                        if val != 0:
                            solution[vid][str(key)] = val
                else:
                    solution[vid] = solver.value(v)

        logger.info(f"CP-SAT: status={status_str}, obj={obj_val}, time={elapsed:.2f}s")

        # Extract best bound for optimality gap calculation
        best_bound = None
        if success:
            try:
                best_bound = solver.best_objective_bound
            except (RuntimeError, AttributeError):
                pass

        # INFEASIBLE 진단
        infeasibility_info = None
        if status == cp_model.INFEASIBLE:
            # SP 모델이면 SP 전용 진단 사용
            if compile_result.metadata.get("model_type") == "SetPartitioning":
                infeasibility_info = self._diagnose_sp_infeasibility(
                    compile_result, solver, elapsed
                )
            else:
                infeasibility_info = self._diagnose_infeasibility(
                    compile_result, solver, elapsed
                )

        return ExecuteResult(
            success=success,
            solver_type="ortools_cp",
            status=status_str,
            objective_value=obj_val,
            best_bound=best_bound,
            solution=solution,
            execution_time_sec=round(elapsed, 3),
            solver_info={
                "branches": solver.num_branches,
                "conflicts": solver.num_conflicts,
                "wall_time": solver.wall_time,
                "num_workers": num_workers,
                "best_bound": best_bound,
            },
            infeasibility_info=infeasibility_info,
        )

    def _execute_lp(self, compile_result, time_limit) -> ExecuteResult:
        solver = compile_result.solver_model
        var_map = compile_result.variable_map

        solver.set_time_limit(time_limit * 1000)

        logger.info(f"LP/MIP: solving with time_limit={time_limit}s")
        start = time.time()
        status = solver.Solve()
        elapsed = time.time() - start

        from ortools.linear_solver import pywraplp
        status_map = {
            pywraplp.Solver.OPTIMAL: "OPTIMAL",
            pywraplp.Solver.FEASIBLE: "FEASIBLE",
            pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
            pywraplp.Solver.UNBOUNDED: "UNBOUNDED",
            pywraplp.Solver.ABNORMAL: "ERROR",
            pywraplp.Solver.NOT_SOLVED: "TIMEOUT",
        }
        status_str = status_map.get(status, "UNKNOWN")
        success = status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE)

        solution = {}
        obj_val = None
        if success:
            obj_val = solver.Objective().Value()
            for vid, v in var_map.items():
                if isinstance(v, dict):
                    solution[vid] = {}
                    for key, var in v.items():
                        val = var.solution_value()
                        if val != 0:
                            solution[vid][str(key)] = val
                else:
                    solution[vid] = v.solution_value()

        logger.info(f"LP/MIP: status={status_str}, obj={obj_val}, time={elapsed:.2f}s")

        # Extract best bound for optimality gap calculation
        best_bound = None
        if success:
            try:
                best_bound = solver.Objective().BestBound()
            except Exception:
                pass

        return ExecuteResult(
            success=success,
            solver_type="ortools_lp",
            status=status_str,
            objective_value=obj_val,
            best_bound=best_bound,
            solution=solution,
            execution_time_sec=round(elapsed, 3),
            solver_info={
                "iterations": solver.iterations(),
                "nodes": solver.nodes(),
                "best_bound": best_bound,
            },
        )

    # ── 범용 INFEASIBLE 진단 ──
    def _diagnose_infeasibility(
        self, compile_result, solver, elapsed: float
    ) -> Dict[str, Any]:
        """
        INFEASIBLE 판정 시 진단 정보를 생성한다.
        - compile_result.metadata["constraint_info"]에서 적용된 제약 목록 추출
        - 솔버 통계(conflicts, branches)로 충돌 규모 추정
        - hard 제약 간 충돌 가능성을 분석하여 사용자에게 안내
        """
        metadata = compile_result.metadata or {}
        constraint_info = metadata.get("constraint_info", [])

        # 제약조건 분류
        hard_constraints = [c for c in constraint_info if c.get("category") == "hard"]
        soft_constraints = [c for c in constraint_info if c.get("category") == "soft"]
        failed_constraints = [c for c in constraint_info if c.get("count", 0) == 0]
        applied_constraints = [c for c in constraint_info if c.get("count", 0) > 0]

        # 총 제약 인스턴스 수
        total_hard_instances = sum(c.get("count", 0) for c in hard_constraints)
        total_soft_instances = sum(c.get("count", 0) for c in soft_constraints)

        # 솔버 통계
        solver_stats = {
            "conflicts": solver.num_conflicts if hasattr(solver, 'num_conflicts') else 0,
            "branches": solver.num_branches if hasattr(solver, 'num_branches') else 0,
            "wall_time": solver.wall_time if hasattr(solver, 'wall_time') else elapsed,
        }

        # 충돌 가능성 분석 (heuristic)
        conflict_hints = []

        # 고정 인원수 + 총 인원수 제약이 동시에 있으면 충돌 가능성
        hard_names = {c["name"] for c in hard_constraints if c.get("count", 0) > 0}
        count_constraints = {n for n in hard_names if "count" in n.lower() or "total" in n.lower()}
        if len(count_constraints) >= 2:
            conflict_hints.append({
                "type": "numeric_conflict",
                "constraints": list(count_constraints),
                "message": "인원수/총량 관련 제약이 여러 개 적용되어 있습니다. 값이 서로 모순되지 않는지 확인하세요.",
            })

        # coverage + assignment 동시 적용 시
        coverage_constraints = {n for n in hard_names if "coverage" in n.lower() or "assign" in n.lower()}
        capacity_constraints = {n for n in hard_names if "capacity" in n.lower() or "max" in n.lower() or "limit" in n.lower()}
        if coverage_constraints and capacity_constraints:
            conflict_hints.append({
                "type": "coverage_capacity_conflict",
                "constraints": list(coverage_constraints | capacity_constraints),
                "message": "할당 의무 제약과 용량 제한 제약이 동시에 적용되어 있습니다. 자원이 부족하면 충돌합니다.",
            })

        # 0 conflicts = 전처리 단계에서 바로 infeasible 판정 (명백한 모순)
        if solver_stats["conflicts"] == 0:
            conflict_hints.append({
                "type": "trivial_infeasibility",
                "message": "솔버가 탐색 없이 즉시 INFEASIBLE을 판정했습니다. 제약조건 값에 명백한 모순이 있을 수 있습니다.",
            })

        diagnosis = {
            "summary": {
                "hard_constraint_count": len(hard_constraints),
                "hard_instance_count": total_hard_instances,
                "soft_constraint_count": len(soft_constraints),
                "soft_instance_count": total_soft_instances,
                "failed_constraint_count": len(failed_constraints),
            },
            "applied_constraints": [
                {"name": c["name"], "category": c["category"], "count": c["count"]}
                for c in applied_constraints
            ],
            "failed_constraints": [
                {"name": c["name"], "category": c["category"]}
                for c in failed_constraints
            ],
            "solver_stats": solver_stats,
            "conflict_hints": conflict_hints,
        }

        logger.info(
            f"INFEASIBLE diagnosis: {len(hard_constraints)} hard constraints "
            f"({total_hard_instances} instances), "
            f"conflicts={solver_stats['conflicts']}, "
            f"hints={len(conflict_hints)}"
        )

        return diagnosis

    def _diagnose_sp_infeasibility(
        self, compile_result, solver, elapsed: float
    ) -> Dict[str, Any]:
        """
        Set Partitioning INFEASIBLE 진단.

        SP 모델은 coverage(==1) + crew count 제약만 있으므로
        원인이 명확히 분류 가능:
        1. crew count 제약 충돌 (day+night != total, 후보 부족)
        2. exact cover 불가능 (column 다양성 부족)
        3. coverage + crew count 동시 충돌
        """
        metadata = compile_result.metadata or {}
        sp_diag = metadata.get("sp_diagnostics", {})
        constraint_risks = sp_diag.get("constraint_risks", [])

        solver_stats = {
            "conflicts": solver.num_conflicts if hasattr(solver, 'num_conflicts') else 0,
            "branches": solver.num_branches if hasattr(solver, 'num_branches') else 0,
            "wall_time": solver.wall_time if hasattr(solver, 'wall_time') else elapsed,
        }

        # ── 원인 분석 ──
        causes = []
        suggestions = []

        # 1. compile 시 감지된 리스크
        for risk in constraint_risks:
            causes.append({
                "type": risk["risk"],
                "constraint": risk.get("constraint", ""),
                "message": risk["message"],
            })

        # 2. column_type 분포 기반 진단
        type_dist = sp_diag.get("column_type_distribution", {})
        if type_dist:
            night_available = type_dist.get("night", 0) + type_dist.get("overnight", 0)
            day_available = type_dist.get("day", 0) + type_dist.get("default", 0)

            if night_available == 0:
                causes.append({
                    "type": "NO_NIGHT_COLUMNS",
                    "message": "야간/숙박조 column이 0개입니다. "
                               "숙박조(overnight) 설정을 확인하세요.",
                })
                suggestions.append("숙박조(is_overnight_crew) 파라미터를 활성화하세요.")

            if night_available < 13:
                suggestions.append(
                    f"야간 column이 {night_available}개로 부족합니다. "
                    f"Generator의 overnight chain 범위를 확장하세요."
                )

        # 3. coverage density 기반
        weak_count = sp_diag.get("weak_tasks_count", 0)
        if weak_count > 0:
            causes.append({
                "type": "WEAK_COVERAGE",
                "message": f"{weak_count}개 task의 coverage가 3개 이하입니다. "
                           f"exact cover 조합이 불가능할 수 있습니다.",
            })
            suggestions.append("Generator beam_width를 늘리거나 max_columns_target을 증가시키세요.")

        # 4. 원인이 없으면 일반적 진단
        if not causes:
            if solver_stats["conflicts"] == 0:
                causes.append({
                    "type": "PRESOLVE_INFEASIBLE",
                    "message": "solver가 탐색 없이 즉시 INFEASIBLE 판정. "
                               "crew count 제약이 현재 column pool과 모순됩니다.",
                })
            else:
                causes.append({
                    "type": "SEARCH_INFEASIBLE",
                    "message": f"탐색 후 INFEASIBLE ({solver_stats['conflicts']} conflicts). "
                               f"exact cover 조합이 현재 column pool에 없습니다.",
                })
            suggestions.append("crew count 제약을 완화하거나, Generator pool 크기를 늘리세요.")

        # ── 사용자 메시지 생성 ──
        user_message = "최적화 실행 불가 (INFEASIBLE):\n"
        for i, cause in enumerate(causes, 1):
            user_message += f"  {i}. {cause['message']}\n"
        if suggestions:
            user_message += "\n제안:\n"
            for s in suggestions:
                user_message += f"  • {s}\n"

        diagnosis = {
            "model_type": "SetPartitioning",
            "causes": causes,
            "suggestions": suggestions,
            "user_message": user_message,
            "sp_diagnostics": sp_diag,
            "solver_stats": solver_stats,
        }

        logger.info(f"SP INFEASIBLE diagnosis: {len(causes)} causes, "
                     f"{len(suggestions)} suggestions")
        for cause in causes:
            logger.warning(f"  SP cause: {cause['message']}")

        return diagnosis
