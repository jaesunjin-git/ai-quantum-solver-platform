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
    all_task_ids: Optional[set] = None,
) -> SetPartitioningProblem:
    """
    Column 목록에서 SetPartitioningProblem 구축.

    Args:
        columns: Generator 출력
        params: bound_data["parameters"] — 추가 제약용 (crew count 등)
        all_task_ids: 전체 task id set (없으면 columns에서 추출)

    Returns:
        SetPartitioningProblem
    """
    params = params or {}

    # ── Contract 검증 (#5) ──
    assert len(columns) > 0, "SP: no columns provided"
    assert all(len(c.trips) > 0 for c in columns), "SP: column with empty trips"
    assert len({c.id for c in columns}) == len(columns), "SP: duplicate column ids"

    # task → column 인덱스 구축
    task_to_columns: Dict[int, List[int]] = {}
    for col in columns:
        for tid in col.trips:
            task_to_columns.setdefault(tid, []).append(col.id)

    task_ids = sorted(task_to_columns.keys())

    # cost 맵
    costs = {col.id: col.cost for col in columns}

    # ── uncovered 진단 (#2: 전체 task set 기반) ──
    covered_tasks = set(task_to_columns.keys())
    if all_task_ids:
        uncovered = sorted(all_task_ids - covered_tasks)
    else:
        uncovered = []  # 전체 task set 미제공 시 감지 불가

    # ── degree_1 진단 (#3: forced selection 분석) ──
    degree_1 = [tid for tid, cids in task_to_columns.items() if len(cids) == 1]

    # 추가 제약 (params 기반)
    extra = _build_extra_constraints(columns, params)

    # ── #3: forced column vs total_duties 충돌 사전 감지 ──
    if degree_1:
        forced_col_ids = set()
        for tid in degree_1:
            forced_col_ids.update(task_to_columns[tid])
        total_con = next((c for c in extra if c.name == "total_columns"), None)
        if total_con and len(forced_col_ids) > total_con.rhs:
            logger.error(
                f"SP: {len(forced_col_ids)} forced columns > total_duties({total_con.rhs}) "
                f"→ INFEASIBLE 확정"
            )

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

    # ── #4: crew count 충돌 사전 검증 ──
    if total is not None and day_count is not None and night_count is not None:
        if int(day_count) + int(night_count) != int(total):
            logger.error(
                f"SP constraint conflict: day({day_count}) + night({night_count}) = "
                f"{int(day_count)+int(night_count)} ≠ total({total})"
            )
        # column 가용성 체크
        if day_count is not None:
            day_available = sum(1 for c in columns if c.column_type in ("day", "default"))
            if day_available < int(day_count):
                logger.error(f"SP: day columns {day_available} < required {day_count}")
        if night_count is not None:
            night_available = sum(1 for c in columns if c.column_type in ("night", "overnight"))
            if night_available < int(night_count):
                logger.error(f"SP: night columns {night_available} < required {night_count}")

    return constraints
