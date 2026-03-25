from .base import BaseExecutor, ExecuteResult
from .ortools_executor import ORToolsExecutor
from .dwave_executor import DWaveExecutor

EXECUTOR_MAP = {
    "ortools_cp": ORToolsExecutor,
    "ortools_lp": ORToolsExecutor,
    "dwave_cqm": None,  # lazy import (D-Wave SDK 선택적)
    "cqm": DWaveExecutor,
    "bqm": DWaveExecutor,
    "nl": DWaveExecutor,
}


def get_executor(solver_type: str) -> BaseExecutor:
    cls = EXECUTOR_MAP.get(solver_type)
    # lazy import for optional dependencies (D-Wave SDK)
    if cls is None and solver_type == "dwave_cqm":
        from engine.executor.cqm_executor import CQMExecutor
        return CQMExecutor()
    if cls is None:
        raise ValueError(f"No executor for solver_type: {solver_type}")
    return cls()
