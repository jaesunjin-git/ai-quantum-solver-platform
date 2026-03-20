"""
sp_problem.py ──────────────────────────────────────────────
Backend-agnostic Set Partitioning 문제 정의.

Column Generator 출력을 solver-independent한 구조로 변환.
각 solver backend(CP-SAT, CQM, BQM, Gurobi 등)는
이 구조를 입력으로 받아 자체 모델을 생성.

구조:
  - columns: 선택 가능한 column 목록 (id, tasks, cost)
  - task_to_columns: task별 커버하는 column 인덱스
  - extra_constraints: 추가 제약 (crew count 등)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.column_generator import FeasibleColumn

logger = logging.getLogger(__name__)


@dataclass
class SPConstraint:
    """추가 제약 (crew count, capacity 등)"""
    name: str
    column_ids: List[int]       # 대상 column id
    operator: str               # "==", "<=", ">="
    rhs: int                    # 우변 값
    label: str = ""             # 로그/디버깅용


@dataclass
class SetPartitioningProblem:
    """
    Solver-agnostic Set Partitioning 문제 정의.

    모든 solver backend가 이 구조를 입력으로 사용.

    구성:
      - columns: FeasibleColumn 목록 (id, tasks, cost)
      - task_to_columns: {task_id: [column_id, ...]}
      - task_ids: 모든 task id (정렬)
      - extra_constraints: 추가 제약 (총 column 수, 타입별 수 등)
    """
    columns: List[FeasibleColumn]
    task_to_columns: Dict[int, List[int]]
    task_ids: List[int]
    costs: Dict[int, float]                           # column_id → cost
    extra_constraints: List[SPConstraint] = field(default_factory=list)

    # 진단 정보
    uncovered_tasks: List[int] = field(default_factory=list)
    degree_1_tasks: List[int] = field(default_factory=list)  # 1개 column에만 포함

    @property
    def num_columns(self) -> int:
        return len(self.columns)

    @property
    def num_tasks(self) -> int:
        return len(self.task_ids)

    @property
    def num_constraints(self) -> int:
        """coverage 제약 + 추가 제약"""
        return len(self.task_ids) + len(self.extra_constraints)

    def validate(self) -> Tuple[bool, List[str]]:
        """문제 유효성 검증. (valid, errors) 반환."""
        errors = []
        if not self.columns:
            errors.append("No columns provided")
        if self.uncovered_tasks:
            errors.append(f"{len(self.uncovered_tasks)} tasks have no covering column: "
                          f"{self.uncovered_tasks[:10]}")
        if self.degree_1_tasks:
            logger.warning(f"SP problem: {len(self.degree_1_tasks)} tasks have degree 1 "
                           f"(forced selection)")
        return len(errors) == 0, errors


def build_sp_problem(
    columns: List[FeasibleColumn],
    params: Optional[Dict[str, Any]] = None,
) -> SetPartitioningProblem:
    """
    Column 목록에서 SetPartitioningProblem 구축.

    Args:
        columns: Generator 출력
        params: bound_data["parameters"] — 추가 제약용 (crew count 등)

    Returns:
        SetPartitioningProblem
    """
    params = params or {}

    # task → column 인덱스 구축
    task_to_columns: Dict[int, List[int]] = {}
    for col in columns:
        for tid in col.trips:
            task_to_columns.setdefault(tid, []).append(col.id)

    task_ids = sorted(task_to_columns.keys())

    # cost 맵
    costs = {col.id: col.cost for col in columns}

    # 진단
    uncovered = [tid for tid, cids in task_to_columns.items() if not cids]
    degree_1 = [tid for tid, cids in task_to_columns.items() if len(cids) == 1]

    # 추가 제약 (params 기반)
    extra = _build_extra_constraints(columns, params)

    problem = SetPartitioningProblem(
        columns=columns,
        task_to_columns=task_to_columns,
        task_ids=task_ids,
        costs=costs,
        extra_constraints=extra,
        uncovered_tasks=uncovered,
        degree_1_tasks=degree_1,
    )

    logger.info(f"SP problem: {problem.num_columns} columns, {problem.num_tasks} tasks, "
                f"{len(extra)} extra constraints, "
                f"uncovered={len(uncovered)}, degree_1={len(degree_1)}")

    return problem


def _build_extra_constraints(
    columns: List[FeasibleColumn],
    params: Dict[str, Any],
) -> List[SPConstraint]:
    """params에서 추가 제약 생성 (crew count 등)"""
    constraints = []
    col_map = {c.id: c for c in columns}

    # 총 column 수 고정
    total = params.get("total_duties")
    if total is not None:
        constraints.append(SPConstraint(
            name="total_columns",
            column_ids=[c.id for c in columns],
            operator="==",
            rhs=int(total),
            label=f"총 column 수 = {int(total)}",
        ))

    # column_type별 수 고정 (day/night)
    day_count = params.get("day_crew_count")
    if day_count is not None:
        day_ids = [c.id for c in columns
                   if c.column_type in ("day", "default")]
        constraints.append(SPConstraint(
            name="day_columns",
            column_ids=day_ids,
            operator="==",
            rhs=int(day_count),
            label=f"day columns = {int(day_count)}",
        ))

    night_count = params.get("night_crew_count")
    if night_count is not None:
        night_ids = [c.id for c in columns
                     if c.column_type in ("night", "overnight")]
        constraints.append(SPConstraint(
            name="night_columns",
            column_ids=night_ids,
            operator="==",
            rhs=int(night_count),
            label=f"night columns = {int(night_count)}",
        ))

    return constraints
