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
        time_limit = kwargs.get("time_limit_sec", 300)

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

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 8

        logger.info(f"CP-SAT: solving with time_limit={time_limit}s")
        start = time.time()
        status = solver.solve(model)
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

        return ExecuteResult(
            success=success,
            solver_type="ortools_cp",
            status=status_str,
            objective_value=obj_val,
            solution=solution,
            execution_time_sec=round(elapsed, 3),
            solver_info={
                "branches": solver.num_branches,
                "conflicts": solver.num_conflicts,
                "wall_time": solver.wall_time,
                "num_workers": 8,
            },
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

        return ExecuteResult(
            success=success,
            solver_type="ortools_lp",
            status=status_str,
            objective_value=obj_val,
            solution=solution,
            execution_time_sec=round(elapsed, 3),
            solver_info={
                "iterations": solver.iterations(),
                "nodes": solver.nodes(),
            },
        )
