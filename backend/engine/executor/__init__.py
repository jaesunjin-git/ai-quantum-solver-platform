from .base import BaseExecutor, ExecuteResult
from .ortools_executor import ORToolsExecutor
from .dwave_executor import DWaveExecutor

EXECUTOR_MAP = {
    "ortools_cp": ORToolsExecutor,
    "ortools_lp": ORToolsExecutor,
    "cqm": DWaveExecutor,
    "bqm": DWaveExecutor,
}


def get_executor(solver_type: str) -> BaseExecutor:
    cls = EXECUTOR_MAP.get(solver_type)
    if cls is None:
        raise ValueError(f"No executor for solver_type: {solver_type}")
    return cls()
