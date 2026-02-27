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

            obj_val = best.energy if hasattr(best, 'energy') else None

            logger.info(f"CQM: status={status}, energy={obj_val}, time={elapsed:.2f}s, feasible={len(feasible)}/{len(sampleset)}")

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
                success=success if obj_val is not None else False,
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
