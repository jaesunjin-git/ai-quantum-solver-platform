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
  - diagnostics: 사전 진단 정보 (모든 backend 공유)
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.column_generator import FeasibleColumn

logger = logging.getLogger(__name__)


# ── Column Type 상수 (하드코딩 문자열 방지) ──────────────────
class ColumnType:
    """column_type 값 상수 — duty_generator, sp_problem, result_converter에서 공유"""
    DAY = "day"
    NIGHT = "night"
    OVERNIGHT = "overnight"
    DEFAULT = "default"

    DAY_GROUP = (DAY, DEFAULT)
    NIGHT_GROUP = (NIGHT, OVERNIGHT)


@dataclass
class CoverageDiagnostics:
    """SP coverage capacity 진단 — solver 호출 전 수학적 feasibility 검증"""
    feasible: bool
    total_tasks: int = 0
    max_columns: Optional[int] = None     # K (partition 수 제약)
    required_avg: float = 0.0             # N/K
    current_avg: float = 0.0             # 실제 column 평균 task 수
    top_k_capacity: int = 0              # 가장 큰 K개 column의 task 수 합
    capacity_gap: int = 0                # total_tasks - top_k_capacity (>0이면 부족)
    type_deficits: Dict[str, int] = field(default_factory=dict)  # 타입별 부족분


@dataclass
class GenerationHint:
    """SP 진단 → Generator로 전달되는 도메인 무관 힌트"""
    min_tasks_per_column: float = 0.0     # column당 최소 task 수
    prefer_longer: bool = False           # 더 긴 column 우선 생성
    capacity_gap: int = 0                 # 추가로 커버해야 하는 task 수
    column_type_deficits: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_diagnostics(cls, diag: CoverageDiagnostics) -> "GenerationHint":
        return cls(
            min_tasks_per_column=diag.required_avg,
            prefer_longer=(diag.current_avg < diag.required_avg),
            capacity_gap=max(0, diag.capacity_gap),
            column_type_deficits=diag.type_deficits,
        )


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
      - diagnostics: 사전 진단 정보 (모든 backend에서 공유)
      - infeasibility_reasons: 사전 감지된 INFEASIBLE 원인
    """
    columns: List[FeasibleColumn]
    task_to_columns: Dict[int, List[int]]
    task_ids: List[int]
    extra_constraints: List[SPConstraint] = field(default_factory=list)

    # 진단 정보
    uncovered_tasks: List[int] = field(default_factory=list)
    degree_1_tasks: List[int] = field(default_factory=list)  # 1개 column에만 포함
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    infeasibility_reasons: List[str] = field(default_factory=list)

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

    @property
    def is_known_infeasible(self) -> bool:
        """사전 감지된 INFEASIBLE 원인이 있는지"""
        return len(self.infeasibility_reasons) > 0

    def diagnose_coverage(self) -> CoverageDiagnostics:
        """SP coverage capacity 진단 — solver 호출 전 수학적 feasibility 검증.
        도메인 지식 불필요, 순수 수학.

        Type 제약이 있으면 type별로 개별 진단:
        - 해당 type에만 존재하는 task (type-exclusive) 수 파악
        - type별 required_avg = type_exclusive_tasks / type_columns
        - type별 avg_tasks < type별 required_avg이면 infeasible
        """
        total_tasks = len(self.task_ids)

        # partition 수 제약 탐색 (total_columns == K)
        total_con = next(
            (c for c in self.extra_constraints if c.name == "total_columns"),
            None,
        )
        if total_con is None:
            return CoverageDiagnostics(feasible=True, total_tasks=total_tasks)

        max_columns = total_con.rhs
        required_avg = total_tasks / max(max_columns, 1)

        # column 크기 분포 (전체)
        col_sizes = sorted(
            (len(c.trips) for c in self.columns), reverse=True
        )
        current_avg = sum(col_sizes) / max(len(col_sizes), 1)

        # ── Type 제약 분석 ──
        type_constraints = [
            c for c in self.extra_constraints
            if c.name in ("day_columns", "night_columns") and c.operator == "=="
        ]

        type_deficits = {}
        all_type_feasible = True

        if type_constraints:
            # task별 커버 가능 type 분석
            task_types: Dict[int, set] = {tid: set() for tid in self.task_ids}
            for col in self.columns:
                if col.column_type in ColumnType.DAY_GROUP:
                    t_label = "day"
                elif col.column_type in ColumnType.NIGHT_GROUP:
                    t_label = "night"
                else:
                    t_label = "other"
                for tid in col.trips:
                    if tid in task_types:
                        task_types[tid].add(t_label)

            # type-exclusive task 계산
            day_exclusive = sum(1 for ts in task_types.values() if ts == {"day"})
            night_exclusive = sum(1 for ts in task_types.values() if ts == {"night"})
            both_types = sum(1 for ts in task_types.values() if "day" in ts and "night" in ts)

            for con in type_constraints:
                type_name = "day" if con.name == "day_columns" else "night"
                type_group = ColumnType.DAY_GROUP if type_name == "day" else ColumnType.NIGHT_GROUP
                type_cols = [c for c in self.columns if c.column_type in type_group]
                type_sizes = sorted((len(c.trips) for c in type_cols), reverse=True)
                type_avg = sum(type_sizes) / max(len(type_sizes), 1)
                type_top_k = sum(type_sizes[:con.rhs])

                # type-exclusive tasks: 이 type의 column에만 존재하는 task 수
                exclusive_count = day_exclusive if type_name == "day" else night_exclusive
                # 이 type이 커버해야 하는 최소 task = exclusive + shared의 일부
                # shared 배분: 각 type의 남은 용량 비율로 배분 (보수적 추정)
                type_required_avg = exclusive_count / max(con.rhs, 1)
                # type-exclusive만으로도 용량 부족하면 확실히 infeasible
                type_feasible = (type_avg >= type_required_avg)

                # 추가: shared tasks까지 고려한 전체 부담 추정
                # day가 커버해야 하는 총 task ≈ day_exclusive + (both × day_share)
                # 보수적: day_share = day_columns / total_columns
                share_ratio = con.rhs / max(max_columns, 1)
                estimated_total_tasks = exclusive_count + int(both_types * share_ratio)
                full_required_avg = estimated_total_tasks / max(con.rhs, 1)

                if type_avg < full_required_avg:
                    type_feasible = False

                if not type_feasible:
                    all_type_feasible = False

                type_deficits[type_name] = {
                    "required": con.rhs,
                    "available_columns": len(type_cols),
                    "avg_tasks": round(type_avg, 1),
                    "top_k_capacity": type_top_k,
                    "exclusive_tasks": exclusive_count,
                    "estimated_total_tasks": estimated_total_tasks,
                    "required_avg": round(full_required_avg, 1),
                    "feasible": type_feasible,
                }

            top_k_capacity = sum(
                td["top_k_capacity"] for td in type_deficits.values()
            )
        else:
            top_k_capacity = sum(col_sizes[:max_columns])

        capacity_gap = max(0, total_tasks - top_k_capacity)

        return CoverageDiagnostics(
            feasible=(capacity_gap == 0 and all_type_feasible),
            total_tasks=total_tasks,
            max_columns=max_columns,
            required_avg=required_avg,
            current_avg=current_avg,
            top_k_capacity=top_k_capacity,
            capacity_gap=capacity_gap,
            type_deficits=type_deficits,
        )

    def should_regenerate(self, params: Optional[Dict] = None) -> bool:
        """ACG: column pool 재생성이 필요한지 판단.
        "재생성으로 풀릴 문제만" 감지 — constraint 충돌은 재생성 무의미."""
        d = self.diagnostics or {}
        params = params or {}

        # 절대 재생성: uncovered task 존재
        if self.uncovered_tasks:
            return True

        # coverage capacity 부족 (solver 호출 전 수학적 검증)
        cov_diag = self.diagnose_coverage()
        if not cov_diag.feasible:
            logger.warning(
                f"Coverage capacity insufficient: "
                f"gap={cov_diag.capacity_gap}, "
                f"required_avg={cov_diag.required_avg:.1f}, "
                f"current_avg={cov_diag.current_avg:.1f}"
            )
            return True

        # 야간 column 부족
        night_needed = params.get("night_crew_count")
        if night_needed is not None:
            night_count = d.get("column_type_distribution", {}).get("night", 0)
            overnight_count = d.get("column_type_distribution", {}).get("overnight", 0)
            if (night_count + overnight_count) < int(night_needed):
                return True

        # 주간 column 부족
        day_needed = params.get("day_crew_count")
        if day_needed is not None:
            day_count = d.get("column_type_distribution", {}).get("day", 0)
            default_count = d.get("column_type_distribution", {}).get("default", 0)
            if (day_count + default_count) < int(day_needed):
                return True

        # singleton 비율 과다 (15% 이상이면 diversity 부족)
        if self.degree_1_tasks and self.task_ids:
            singleton_ratio = len(self.degree_1_tasks) / len(self.task_ids)
            if singleton_ratio > 0.15:
                return True

        return False

    def validate(self) -> Tuple[bool, List[str], List[str]]:
        """
        문제 유효성 검증. (valid, errors, warnings) 반환.
        Side effect 없음 (로깅은 caller 책임).
        """
        errors = list(self.infeasibility_reasons)  # 사전 감지 원인 포함

        if not self.columns:
            errors.append("No columns provided")
        if self.uncovered_tasks:
            errors.append(
                f"{len(self.uncovered_tasks)} tasks have no covering column: "
                f"{self.uncovered_tasks[:10]}"
            )

        warnings = []
        if self.degree_1_tasks:
            warnings.append(
                f"{len(self.degree_1_tasks)} tasks have degree 1 (forced selection)"
            )

        return len(errors) == 0, errors, warnings


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

    Raises:
        ValueError: 입력 데이터 무결성 위반
    """
    params = params or {}

    # ── Contract 검증: 명시적 예외 + 단일 순회 통합 ──
    if not columns:
        raise ValueError("SP: no columns provided")

    seen_ids: set = set()
    task_to_columns: Dict[int, List[int]] = {}

    for col in columns:
        if not col.trips:
            raise ValueError(f"SP: column {col.id} has empty trips")
        if col.id in seen_ids:
            raise ValueError(f"SP: duplicate column id: {col.id}")
        seen_ids.add(col.id)
        # task → column 인덱스 구축 (단일 순회에서 동시 처리)
        for tid in col.trips:
            task_to_columns.setdefault(tid, []).append(col.id)

    task_ids = sorted(task_to_columns.keys())

    # ── uncovered 진단: 전체 task set 기반 ──
    covered_tasks = set(task_to_columns.keys())
    if all_task_ids:
        uncovered = sorted(all_task_ids - covered_tasks)
    else:
        uncovered = []

    # ── degree_1 진단: forced selection 분석 ──
    degree_1 = [tid for tid, cids in task_to_columns.items() if len(cids) == 1]

    # ── 추가 제약 생성 (params 기반) ──
    extra = _build_extra_constraints(columns, params)

    # ── 제약 검증 → infeasibility_reasons 수집 ──
    infeasibility_reasons = _validate_constraints(extra, columns, params)

    # forced column vs total_duties 충돌 사전 감지
    if degree_1:
        forced_col_ids = set()
        for tid in degree_1:
            forced_col_ids.update(task_to_columns[tid])
        total_con = next((c for c in extra if c.name == "total_columns"), None)
        if total_con and len(forced_col_ids) > total_con.rhs:
            infeasibility_reasons.append(
                f"forced columns ({len(forced_col_ids)}) > total_duties ({total_con.rhs})"
            )

    problem = SetPartitioningProblem(
        columns=columns,
        task_to_columns=task_to_columns,
        task_ids=task_ids,
        extra_constraints=extra,
        uncovered_tasks=uncovered,
        degree_1_tasks=degree_1,
        infeasibility_reasons=infeasibility_reasons,
    )

    # ── 진단 정보 구축 (모든 backend 공유) ──
    problem.diagnostics = build_sp_diagnostics(problem)

    logger.info(f"SP problem: {problem.num_columns} columns, {problem.num_tasks} tasks, "
                f"{len(extra)} extra constraints, "
                f"uncovered={len(uncovered)}, degree_1={len(degree_1)}")
    if infeasibility_reasons:
        for reason in infeasibility_reasons:
            logger.error(f"SP known infeasible: {reason}")

    return problem


def _build_extra_constraints(
    columns: List[FeasibleColumn],
    params: Dict[str, Any],
) -> List[SPConstraint]:
    """params에서 추가 제약 생성 (crew count 등). 생성만 담당."""
    constraints = []

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
                   if c.column_type in ColumnType.DAY_GROUP]
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
                     if c.column_type in ColumnType.NIGHT_GROUP]
        constraints.append(SPConstraint(
            name="night_columns",
            column_ids=night_ids,
            operator="==",
            rhs=int(night_count),
            label=f"night columns = {int(night_count)}",
        ))

    return constraints


def _validate_constraints(
    constraints: List[SPConstraint],
    columns: List[FeasibleColumn],
    params: Dict[str, Any],
) -> List[str]:
    """제약 간 충돌 검증. infeasibility reasons 반환."""
    reasons = []

    total = params.get("total_duties")
    day_count = params.get("day_crew_count")
    night_count = params.get("night_crew_count")

    if total is not None and day_count is not None and night_count is not None:
        if int(day_count) + int(night_count) != int(total):
            reasons.append(
                f"day({day_count}) + night({night_count}) = "
                f"{int(day_count) + int(night_count)} ≠ total({total})"
            )

        day_available = sum(1 for c in columns if c.column_type in ColumnType.DAY_GROUP)
        if day_available < int(day_count):
            reasons.append(f"day columns available ({day_available}) < required ({day_count})")

        night_available = sum(1 for c in columns if c.column_type in ColumnType.NIGHT_GROUP)
        if night_available < int(night_count):
            reasons.append(f"night columns available ({night_available}) < required ({night_count})")

    return reasons


def build_sp_diagnostics(problem: SetPartitioningProblem) -> Dict[str, Any]:
    """
    SP 문제 사전 진단 정보 구축 (모든 backend에서 공유).

    INFEASIBLE 발생 시 사용자에게 구체적 원인 제공:
    - coverage 밀도 (어떤 task가 취약한지)
    - crew count 실현 가능성 (column_type 분포)
    - 잠재적 충돌 (제약 간 모순)
    """
    # column_type별 수
    type_dist = Counter(c.column_type for c in problem.columns)

    # task별 coverage density
    density = {tid: len(cids) for tid, cids in problem.task_to_columns.items()}
    min_density = min(density.values()) if density else 0
    weak_tasks = [tid for tid, d in density.items() if d <= 3]

    # extra constraint 실현 가능성 체크
    constraint_risks = []
    for con in problem.extra_constraints:
        available = len(con.column_ids)
        if con.operator == "==" and available < con.rhs:
            constraint_risks.append({
                "constraint": con.name,
                "label": con.label,
                "required": con.rhs,
                "available_columns": available,
                "risk": "INFEASIBLE_CERTAIN",
                "message": f"{con.label}: 필요한 {con.rhs}개보다 후보가 {available}개 부족",
            })
        elif con.operator == "==" and available < con.rhs * 2:
            constraint_risks.append({
                "constraint": con.name,
                "label": con.label,
                "required": con.rhs,
                "available_columns": available,
                "risk": "INFEASIBLE_LIKELY",
                "message": f"{con.label}: 후보 {available}개로 {con.rhs}개 선택은 매우 빡빡",
            })

    # 복합 제약 충돌 체크 (total = day + night)
    total_con = next((c for c in problem.extra_constraints if c.name == "total_columns"), None)
    day_con = next((c for c in problem.extra_constraints if c.name == "day_columns"), None)
    night_con = next((c for c in problem.extra_constraints if c.name == "night_columns"), None)
    if total_con and day_con and night_con:
        if day_con.rhs + night_con.rhs != total_con.rhs:
            constraint_risks.append({
                "constraint": "crew_count_sum",
                "risk": "INFEASIBLE_CERTAIN",
                "message": f"day({day_con.rhs}) + night({night_con.rhs}) = "
                           f"{day_con.rhs + night_con.rhs} ≠ total({total_con.rhs})",
            })

    diagnostics = {
        "column_type_distribution": dict(type_dist),
        "task_count": problem.num_tasks,
        "column_count": problem.num_columns,
        "min_coverage_density": min_density,
        "weak_tasks_count": len(weak_tasks),
        "weak_tasks_sample": weak_tasks[:10],
        "degree_1_count": len(problem.degree_1_tasks),
        "constraint_risks": constraint_risks,
    }

    # 리스크 경고 로그
    for risk in constraint_risks:
        if risk["risk"] == "INFEASIBLE_CERTAIN":
            logger.error(f"SP diagnostic: {risk['message']}")
        elif risk["risk"] == "INFEASIBLE_LIKELY":
            logger.warning(f"SP diagnostic: {risk['message']}")

    return diagnostics
