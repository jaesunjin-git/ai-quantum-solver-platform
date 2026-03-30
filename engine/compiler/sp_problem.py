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
    MORNING_ONLY = "morning_only"
    DEFAULT = "default"

    DAY_GROUP = (DAY, DEFAULT)
    NIGHT_GROUP = (NIGHT, OVERNIGHT, MORNING_ONLY)


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
    seed_trips: Optional[List[int]] = None  # bottleneck trip seeds (diversity용)

    @classmethod
    def from_diagnostics(
        cls, diag: CoverageDiagnostics,
        bottleneck_trips: Optional[List[int]] = None,
    ) -> "GenerationHint":
        return cls(
            min_tasks_per_column=diag.required_avg,
            prefer_longer=(diag.current_avg < diag.required_avg),
            capacity_gap=max(0, diag.capacity_gap),
            column_type_deficits=diag.type_deficits,
            seed_trips=bottleneck_trips,
        )


@dataclass
class SPConstraint:
    """추가 제약 (crew count, capacity, cardinality, aggregate 등)

    기본: Σ z[k] op rhs  (k ∈ column_ids)
    확장: Σ coeff[k] * z[k] op rhs  (coefficients가 있을 때)

    coefficients가 None이면 기존 동작 (모든 coeff = 1).
    backward compatible 확장.
    """
    name: str
    column_ids: List[int]       # 대상 column id
    operator: str               # "==", "<=", ">="
    rhs: float                  # 우변 값 (int → float 확장, aggregate_avg 지원)
    label: str = ""             # 로그/디버깅용
    coefficients: Optional[Dict[int, float]] = None  # column_id → coeff (None=모두 1)
    constraint_ref: str = ""    # constraints.yaml 제약 ID (추적성)


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

    def diagnose_coverage(self, use_top_k: bool = False) -> CoverageDiagnostics:
        """SP coverage capacity 진단 — solver 호출 전 수학적 feasibility 검증.
        도메인 지식 불필요, 순수 수학.

        핵심 원리: trip에는 type이 없고 column(duty)에만 type이 있다.
        trip의 type 친화도(affinity)는 column pool에서 파생되는 통계이다.

        Three-bucket 분석:
          1. type_A_only: type A column에만 존재하는 task → A가 반드시 커버
          2. type_B_only: type B column에만 존재하는 task → B가 반드시 커버
          3. flexible: 양쪽 모두 가능 → 잔여 용량으로 배분
        """
        total_tasks = len(self.task_ids)

        total_con = next(
            (c for c in self.extra_constraints if c.name == "total_columns"),
            None,
        )
        if total_con is None:
            return CoverageDiagnostics(feasible=True, total_tasks=total_tasks)

        max_columns = total_con.rhs
        required_avg = total_tasks / max(max_columns, 1)

        col_sizes = sorted(
            (len(c.trips) for c in self.columns), reverse=True
        )
        current_avg = sum(col_sizes) / max(len(col_sizes), 1)

        # ── Type 제약 분석 (Column-Type Affinity 기반) ──
        type_constraints = [
            c for c in self.extra_constraints
            if c.name in ("day_columns", "night_columns") and c.operator == "=="
        ]

        type_deficits = {}

        if type_constraints:
            # task별 affinity: 어떤 column type에 포함되는지
            task_affinity: Dict[int, Dict[str, int]] = {
                tid: {"day": 0, "night": 0} for tid in self.task_ids
            }
            for col in self.columns:
                if col.column_type in ColumnType.DAY_GROUP:
                    t_label = "day"
                elif col.column_type in ColumnType.NIGHT_GROUP:
                    t_label = "night"
                else:
                    continue
                for tid in col.trips:
                    if tid in task_affinity:
                        task_affinity[tid][t_label] += 1

            # Three-bucket 분류
            day_only_tasks = sum(
                1 for a in task_affinity.values()
                if a["day"] > 0 and a["night"] == 0
            )
            night_only_tasks = sum(
                1 for a in task_affinity.values()
                if a["night"] > 0 and a["day"] == 0
            )
            flexible_tasks = sum(
                1 for a in task_affinity.values()
                if a["day"] > 0 and a["night"] > 0
            )

            # type별 용량 계산
            for con in type_constraints:
                type_name = "day" if con.name == "day_columns" else "night"
                type_group = (
                    ColumnType.DAY_GROUP if type_name == "day"
                    else ColumnType.NIGHT_GROUP
                )
                type_cols = [
                    c for c in self.columns if c.column_type in type_group
                ]
                type_sizes = sorted(
                    (len(c.trips) for c in type_cols), reverse=True
                )
                type_avg = (
                    sum(type_sizes) / len(type_sizes) if type_sizes else 0
                )
                type_top_k = sum(type_sizes[: con.rhs])

                # guaranteed load: 이 type만 커버 가능한 task
                guaranteed = (
                    day_only_tasks if type_name == "day" else night_only_tasks
                )
                # 이 type의 총 용량
                # use_top_k: top-K column의 실제 capacity (balance_workload용)
                # 기본: 전체 평균 기반 (minimize_duties용 — 보수적)
                if use_top_k:
                    capacity = type_top_k  # top-K column들의 trip 수 합
                    top_k_avg = type_top_k / max(con.rhs, 1)
                else:
                    capacity = con.rhs * type_avg
                    top_k_avg = type_avg
                # 잔여 용량 (flexible task 배분 가능)
                remaining_for_flexible = max(0, capacity - guaranteed)

                type_deficits[type_name] = {
                    "required": con.rhs,
                    "available_columns": len(type_cols),
                    "avg_tasks": round(type_avg, 1),
                    "top_k_capacity": type_top_k,
                    "guaranteed_load": guaranteed,
                    "capacity": round(capacity, 0),
                    "remaining_for_flexible": round(remaining_for_flexible, 0),
                }

            # flexible gap: flexible tasks를 양쪽 잔여 용량으로 커버 가능?
            total_remaining = sum(
                td["remaining_for_flexible"] for td in type_deficits.values()
            )
            flexible_gap = max(0, flexible_tasks - int(total_remaining))
            all_feasible = (flexible_gap == 0)

            # type_deficits에 three-bucket 요약 추가
            type_deficits["_summary"] = {
                "day_only_tasks": day_only_tasks,
                "night_only_tasks": night_only_tasks,
                "flexible_tasks": flexible_tasks,
                "total_remaining_for_flexible": round(total_remaining, 0),
                "flexible_gap": flexible_gap,
            }

            top_k_capacity = sum(
                td["top_k_capacity"]
                for k, td in type_deficits.items()
                if k != "_summary"
            )
        else:
            top_k_capacity = sum(col_sizes[:max_columns])
            flexible_gap = 0
            all_feasible = True

        capacity_gap = max(0, total_tasks - top_k_capacity)

        return CoverageDiagnostics(
            feasible=(capacity_gap == 0 and all_feasible),
            total_tasks=total_tasks,
            max_columns=max_columns,
            required_avg=required_avg,
            current_avg=current_avg,
            top_k_capacity=top_k_capacity,
            capacity_gap=max(capacity_gap, flexible_gap),
            type_deficits=type_deficits,
        )

    def should_regenerate(self, params: Optional[Dict] = None,
                          use_top_k: bool = False) -> bool:
        """ACG: column pool 재생성이 필요한지 판단.
        "재생성으로 풀릴 문제만" 감지 — constraint 충돌은 재생성 무의미.

        Args:
            params: bound_data["parameters"]
            use_top_k: balance_workload 등 top-K 기반 진단 사용 여부
                       (Pipeline의 diagnose_coverage 호출과 일치해야 함)
        """
        d = self.diagnostics or {}
        params = params or {}

        # 절대 재생성: uncovered task 존재
        if self.uncovered_tasks:
            return True

        # coverage capacity 부족 (solver 호출 전 수학적 검증)
        cov_diag = self.diagnose_coverage(use_top_k=use_top_k)
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
    objective_type: str = "minimize_duties",
) -> SetPartitioningProblem:
    """
    Column 목록에서 SetPartitioningProblem 구축.

    Args:
        columns: Generator 출력
        params: bound_data["parameters"] — 추가 제약용 (crew count 등)
        all_task_ids: 전체 task id set (없으면 columns에서 추출)
        objective_type: 목적함수 유형 — 제약 연산자 결정에 사용

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

    # ── 추가 제약 생성 (params 기반 — 기존 crew count 등) ──
    extra = _build_extra_constraints(columns, params, objective_type)

    # ── YAML Side Constraint Pipeline ──
    domain = params.get("_domain")
    yaml_constraints = _build_yaml_side_constraints(columns, params, domain)
    extra.extend(yaml_constraints)

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


def _build_yaml_side_constraints(
    columns: List[FeasibleColumn], params: Dict, domain: Optional[str] = None
) -> List[SPConstraint]:
    """YAML engine_config.yaml의 side_constraints 섹션에서 제약 생성."""
    try:
        from engine.config_loader import load_side_constraints
        from engine.constraints.builtin import register_builtin_handlers  # noqa — 자동 등록
        from engine.constraints.base import SideConstraintPipeline

        config_list = load_side_constraints(domain)
        if not config_list:
            return []

        pipeline = SideConstraintPipeline(config_list)
        return pipeline.build_all(columns, params)
    except Exception as e:
        logger.warning(f"YAML side constraints build failed: {e}")
        return []


def _estimate_duty_upper_bound(columns: List[FeasibleColumn], params: Dict) -> int:
    """데이터 기반 duty 수 상한 자동 추정 (사용자 값 없을 때).
    범용: trip duration과 max_active_time만 사용."""
    import math

    total_driving = sum(
        max((c.active_minutes for c in columns if len(c.trips) == 1), default=0)
        for _ in [1]
    )
    # 전체 unique task의 총 driving time
    task_driving: Dict[int, int] = {}
    for c in columns:
        for i, tid in enumerate(c.trips):
            if tid not in task_driving:
                task_driving[tid] = 0
    # 간접 추정: column pool의 평균 active * 평균 task 수 → 전체 driving 추정
    if columns:
        avg_active = sum(c.active_minutes for c in columns) / len(columns)
        avg_tasks = sum(len(c.trips) for c in columns) / len(columns)
        task_count = len(task_driving) if task_driving else len(set(
            tid for c in columns for tid in c.trips
        ))
        estimated_total_driving = task_count * (avg_active / max(avg_tasks, 1))
    else:
        estimated_total_driving = 0
        task_count = 0

    max_active = int(params.get("max_driving_minutes", params.get("max_active_time", 360)))

    # 이론적 하한
    lower_bound = math.ceil(estimated_total_driving / max(max_active, 1))

    # 상한: 하한의 1.4배 (YAML 외부화 가능)
    upper_bound = max(math.ceil(lower_bound * 1.4), lower_bound + 5)

    logger.info(
        f"Auto-estimated duty bounds: lower={lower_bound}, upper={upper_bound} "
        f"(tasks={task_count}, est_driving={estimated_total_driving:.0f}, "
        f"max_active={max_active})"
    )
    return upper_bound


def _build_extra_constraints(
    columns: List[FeasibleColumn],
    params: Dict[str, Any],
    objective_type: str = "minimize_duties",
) -> List[SPConstraint]:
    """params + objective_type에서 추가 제약 생성.

    objective별 연산자 분기:
      balance_workload: == (등호 — 정확한 crew count 강제)
      minimize_duties:  <= (상한 — solver가 더 적은 duty 탐색 가능)
      기타:             <= (상한)
    """
    constraints = []

    # objective별 제약 범위 결정
    #   balance_workload: total==, day==, night== (정확한 crew count 강제)
    #   minimize_duties:  total<= (상한만, day/night 분배는 solver 자유)
    #   기타:             total<=, day<=, night<= (상한)
    if objective_type == "balance_workload":
        apply_total, apply_day, apply_night = True, True, True
        total_op, day_op, night_op = "==", "==", "=="
    elif objective_type in ("minimize_duties", "minimize_duties_with_penalties"):
        apply_total, apply_day, apply_night = True, False, False
        total_op = "<="
        day_op = night_op = None  # 사용하지 않음
    else:
        apply_total, apply_day, apply_night = True, True, True
        total_op, day_op, night_op = "<=", "<=", "<="

    logger.info(
        f"SP extra constraints: objective={objective_type}, "
        f"total={total_op}, day={'skip' if not apply_day else day_op}, "
        f"night={'skip' if not apply_night else night_op}"
    )

    # 총 column 수
    total = params.get("total_duties")
    if apply_total and total is not None:
        constraints.append(SPConstraint(
            name="total_columns",
            column_ids=[c.id for c in columns],
            operator=total_op,
            rhs=int(total),
            label=f"총 column 수 {total_op} {int(total)}",
        ))
    elif apply_total and objective_type != "balance_workload":
        # total_duties 미제공 + minimize 모드 → day+night 합산 또는 자동 추정
        day_val = params.get("day_crew_count")
        night_val = params.get("night_crew_count")
        if day_val is not None and night_val is not None:
            auto_total = int(day_val) + int(night_val)
            constraints.append(SPConstraint(
                name="total_columns",
                column_ids=[c.id for c in columns],
                operator="<=",
                rhs=auto_total,
                label=f"총 column 수 <= {auto_total} (day+night)",
            ))

    # day column 수 (balance_workload에서만 적용)
    if apply_day:
        day_count = params.get("day_crew_count")
        if day_count is not None:
            day_ids = [c.id for c in columns
                       if c.column_type in ColumnType.DAY_GROUP]
            constraints.append(SPConstraint(
                name="day_columns",
                column_ids=day_ids,
                operator=day_op,
                rhs=int(day_count),
                label=f"day columns {day_op} {int(day_count)}",
            ))

    # night column 수 (balance_workload에서만 적용)
    if apply_night:
        night_count = params.get("night_crew_count")
        if night_count is not None:
            night_ids = [c.id for c in columns
                         if c.column_type in ColumnType.NIGHT_GROUP]
            constraints.append(SPConstraint(
                name="night_columns",
                column_ids=night_ids,
                operator=night_op,
                rhs=int(night_count),
                label=f"night columns {night_op} {int(night_count)}",
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
