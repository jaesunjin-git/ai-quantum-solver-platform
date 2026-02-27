# engine/executor/base.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecuteResult:
    success: bool
    solver_type: str = ""
    status: str = ""                    # OPTIMAL, FEASIBLE, INFEASIBLE, TIMEOUT, ERROR
    objective_value: Optional[float] = None
    solution: Dict[str, Any] = field(default_factory=dict)
    execution_time_sec: float = 0.0
    solver_info: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    raw_response: Any = None


class BaseExecutor(ABC):

    @abstractmethod
    def execute(self, compile_result, **kwargs) -> ExecuteResult:
        ...
