"""
quickxplain.py ──────────────────────────────────────────────
QuickXPlain 기반 최소 충돌 부분집합(MUS) 탐색.

교차 충돌(cross-boundary conflict) 문제를 해결하기 위해
단순 Binary Search 대신 QuickXPlain 알고리즘을 사용한다.

QuickXPlain 핵심:
  - background(항상 유지) + test(탐색 대상) 분리
  - background를 유지한 채 test를 절반씩 추가하며
    INFEASIBLE로 전환되는 지점(phase transition)을 탐색
  - O(k·log(N/k)) 복잡도 (k = 실제 충돌 수, 대부분 k≪N)

안전장치:
  - MAX_SOLVES: 총 solve 호출 상한 (기본 50)
  - MAX_DEPTH: 탐색 깊이 상한 (기본 10)
  - iterative(stack) 방식으로 Python 재귀 제한 회피
  - 시간 예산 초과 시 approximate 결과 반환

결과 보장:
  - 시간 내 완료 시: minimal (QuickXPlain 보장)
  - 시간/횟수 초과 시: approximate (보수적 상위 집합)
  - validate_conflict_set()으로 재검증 가능
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from engine.validation.generic.presolve_models import (
    ConflictDiagnosis,
    ConflictEntry,
    GuaranteeLevel,
    SoftTestResult,
)

logger = logging.getLogger(__name__)


# ── QuickXPlain State ────────────────────────────────────────

@dataclass
class _XPlainState:
    """QuickXPlain 실행 상태 (전역 가드용)"""
    solve_count: int = 0
    max_depth_reached: int = 0
    start_time: float = 0.0
    max_solves: int = 50
    max_depth: int = 10
    time_budget_sec: float = 20.0
    per_solve_sec: float = 3.0
    exceeded: bool = False  # 가드 초과 여부

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def check_budget(self) -> bool:
        """예산 초과 여부 확인. True면 계속 진행 가능."""
        if self.solve_count >= self.max_solves:
            self.exceeded = True
            return False
        if self.elapsed >= self.time_budget_sec:
            self.exceeded = True
            return False
        return True


# ── Feasibility Test Helper ──────────────────────────────────

def _is_infeasible(
    constraint_names: List[str],
    math_model: Dict,
    bound_data: Dict,
    state: _XPlainState,
) -> bool:
    """
    주어진 제약 집합만으로 CP-SAT 모델을 빌드하고 feasibility 판정.

    Args:
        constraint_names: 포함할 제약 이름 목록
        math_model: 원본 수학 모델 IR
        bound_data: 바인딩된 데이터
        state: 전역 상태 (solve count 추적)

    Returns:
        True if INFEASIBLE
    """
    if not state.check_budget():
        return False  # 예산 초과 시 보수적으로 feasible 가정

    state.solve_count += 1

    try:
        from ortools.sat.python import cp_model as cp_module

        # 지정된 제약만 포함하는 부분 수학 모델 생성
        name_set = set(constraint_names)
        partial_model = dict(math_model)
        partial_model["constraints"] = [
            c for c in math_model.get("constraints", [])
            if c.get("name", c.get("id", "")) in name_set
        ]

        # CP-SAT 빌드 (canonical_cpsat_builder 재사용)
        from engine.validation.generic.canonical_cpsat_builder import (
            build_cpsat_for_presolve,
        )
        probe_model, _ = build_cpsat_for_presolve(partial_model, bound_data)

        # Quick solve
        solver = cp_module.CpSolver()
        solver.parameters.max_time_in_seconds = state.per_solve_sec
        solver.parameters.num_search_workers = 1
        solver.parameters.log_search_progress = False

        status = solver.solve(probe_model)
        return status == cp_module.INFEASIBLE

    except Exception as e:
        logger.warning(f"L5:presolve:solve_error constraint_count={len(constraint_names)} error={e}")
        return False  # 에러 시 보수적으로 feasible 가정


# ── QuickXPlain (Iterative / Stack 기반) ─────────────────────

@dataclass
class _StackFrame:
    """QuickXPlain iterative 실행을 위한 스택 프레임"""
    background: List[str]
    constraints: List[str]
    depth: int
    # 부분 결과 수집용
    phase: str = "init"  # init | left_done | right_done
    left_result: Optional[List[str]] = None
    right_result: Optional[List[str]] = None


def quickxplain(
    all_constraint_names: List[str],
    math_model: Dict,
    bound_data: Dict,
    max_solves: int = 50,
    max_depth: int = 10,
    time_budget_sec: float = 20.0,
    per_solve_sec: float = 3.0,
) -> Tuple[List[str], GuaranteeLevel, int, int]:
    """
    QuickXPlain: 최소 충돌 부분집합(MUS) 탐색 (iterative 구현).

    전제: all_constraint_names 전체가 INFEASIBLE

    Args:
        all_constraint_names: 탐색 대상 제약 이름 목록
        math_model: 원본 수학 모델 IR
        bound_data: 바인딩된 데이터
        max_solves: 총 solve 호출 상한
        max_depth: 탐색 깊이 상한
        time_budget_sec: 총 시간 예산 (초)
        per_solve_sec: 개별 solve 시간 제한 (초)

    Returns:
        (conflict_set, guarantee_level, solve_count, max_depth_reached)
    """
    state = _XPlainState(
        start_time=time.time(),
        max_solves=max_solves,
        max_depth=max_depth,
        time_budget_sec=time_budget_sec,
        per_solve_sec=per_solve_sec,
    )

    if len(all_constraint_names) <= 1:
        return (
            all_constraint_names,
            GuaranteeLevel.MINIMAL,
            0,
            0,
        )

    # 전체가 정말 infeasible인지 확인
    if not _is_infeasible(all_constraint_names, math_model, bound_data, state):
        return [], GuaranteeLevel.MINIMAL, state.solve_count, 0

    result = _quickxplain_iterative(
        background=[],
        constraints=all_constraint_names,
        math_model=math_model,
        bound_data=bound_data,
        state=state,
    )

    guarantee = (
        GuaranteeLevel.APPROXIMATE if state.exceeded
        else GuaranteeLevel.MINIMAL
    )

    return result, guarantee, state.solve_count, state.max_depth_reached


def _quickxplain_iterative(
    background: List[str],
    constraints: List[str],
    math_model: Dict,
    bound_data: Dict,
    state: _XPlainState,
) -> List[str]:
    """
    QuickXPlain iterative 구현 (스택 기반, 재귀 제한 회피).

    알고리즘:
      1. |constraints| ≤ 1 → 자신이 충돌 원인
      2. constraints를 C1, C2로 분할
      3. background + C1이 INFEASIBLE → C1 안에서 탐색
      4. 아니면 → C1을 background에 추가하고 C2에서 탐색
      5. 양쪽 결과를 합산

    교차 충돌 대응:
      - 한쪽에서 발견된 충돌을 background에 추가한 뒤 반대편도 탐색
      - 이로써 c2↔c7 같은 교차 충돌을 놓치지 않음
    """
    # 스택 기반 반복 (iterative DFS)
    # 각 프레임은 (background, constraints, depth) + 부분 결과
    stack: List[_StackFrame] = [
        _StackFrame(background=background, constraints=constraints, depth=0)
    ]
    final_result: List[str] = []

    while stack:
        if not state.check_budget():
            # 예산 초과: 현재 스택의 모든 constraints를 결과에 추가 (보수적)
            for frame in stack:
                final_result.extend(frame.constraints)
            break

        frame = stack[-1]
        state.max_depth_reached = max(state.max_depth_reached, frame.depth)

        # Base case: 제약 0~1개
        if len(frame.constraints) <= 1:
            stack.pop()
            if stack:
                _collect_result(stack[-1], frame.constraints)
            else:
                final_result.extend(frame.constraints)
            continue

        # Depth guard
        if frame.depth >= state.max_depth:
            state.exceeded = True
            stack.pop()
            if stack:
                _collect_result(stack[-1], frame.constraints)
            else:
                final_result.extend(frame.constraints)
            continue

        if frame.phase == "init":
            # Split
            mid = len(frame.constraints) // 2
            c1 = frame.constraints[:mid]
            c2 = frame.constraints[mid:]

            # Test: background + c1 이 infeasible?
            if _is_infeasible(frame.background + c1, math_model, bound_data, state):
                # 충돌이 c1 안에 있음 → c1 먼저 탐색
                frame.phase = "left_done"
                stack.append(_StackFrame(
                    background=frame.background,
                    constraints=c1,
                    depth=frame.depth + 1,
                ))
                # c2는 left 결과 후에 탐색 (frame에 c2 저장)
                frame._c2 = c2  # type: ignore
            else:
                # c1만으로는 infeasible 아님 → c1을 background에 추가, c2 탐색
                frame.phase = "left_done"
                frame.left_result = []  # c1에서는 충돌 없음
                stack.append(_StackFrame(
                    background=frame.background + c1,
                    constraints=c2,
                    depth=frame.depth + 1,
                ))
                frame._c2 = c1  # 반대편 (나중에 탐색)  # type: ignore

        elif frame.phase == "left_done":
            # left 탐색 완료 → right 탐색
            if frame.left_result is None:
                # left가 방금 완료됨 (결과는 _collect_result로 수집됨)
                pass

            # right 탐색: left 결과를 background에 추가
            c2 = getattr(frame, "_c2", [])
            left = frame.left_result or []

            frame.phase = "right_done"
            if c2:
                stack.append(_StackFrame(
                    background=frame.background + left,
                    constraints=c2,
                    depth=frame.depth + 1,
                ))
            else:
                frame.right_result = []

        elif frame.phase == "right_done":
            # 양쪽 모두 완료 → 결과 합산
            left = frame.left_result or []
            right = frame.right_result or []
            combined = left + right

            stack.pop()
            if stack:
                _collect_result(stack[-1], combined)
            else:
                final_result.extend(combined)

    # 중복 제거
    seen: Set[str] = set()
    deduped: List[str] = []
    for c in final_result:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    return deduped


def _collect_result(parent: _StackFrame, child_result: List[str]) -> None:
    """자식 프레임의 결과를 부모 프레임에 수집"""
    if parent.phase == "left_done" and parent.left_result is None:
        parent.left_result = child_result
    elif parent.phase == "right_done" and parent.right_result is None:
        parent.right_result = child_result
    else:
        # fallback: left에 추가
        if parent.left_result is None:
            parent.left_result = child_result
        elif parent.right_result is None:
            parent.right_result = child_result


# ── 우선순위 정렬 ────────────────────────────────────────────

def rank_constraints_by_risk(
    constraint_names: List[str],
    math_model: Dict,
) -> List[str]:
    """
    high-risk 제약을 앞에 배치 → QuickXPlain이 더 빠르게 수렴.

    우선순위: temporal > capacity > coverage > equality > 기타
    """
    risk_scores: Dict[str, int] = {}
    constraint_map = {
        c.get("name", c.get("id", "")): c
        for c in math_model.get("constraints", [])
    }

    for cname in constraint_names:
        cdef = constraint_map.get(cname, {})
        score = 0
        category = cdef.get("category", cdef.get("priority", "")).lower()
        operator = cdef.get("operator", "")

        # 카테고리 기반 점수
        if "temporal" in category or "time" in cname.lower():
            score += 3
        if "capacity" in category or "capacity" in cname.lower():
            score += 2
        if "coverage" in category or "coverage" in cname.lower():
            score += 2

        # 등식 제약은 충돌 가능성 높음
        if operator == "==":
            score += 2

        # hard 제약 우선
        if category == "hard":
            score += 1

        risk_scores[cname] = score

    return sorted(constraint_names, key=lambda c: risk_scores.get(c, 0), reverse=True)


# ── 재검증 (Validation Pass) ────────────────────────────────

def validate_conflict_set(
    conflict_set: List[str],
    math_model: Dict,
    bound_data: Dict,
    per_solve_sec: float = 3.0,
) -> GuaranteeLevel:
    """
    QuickXPlain 결과를 재검증하여 guarantee level을 확정.

    검증 조건:
      1. conflict_set 전체가 INFEASIBLE인가?
      2. 하나라도 빼면 FEASIBLE인가? (minimal 여부)

    Returns:
        VERIFIED_MINIMAL | APPROXIMATE
    """
    if len(conflict_set) <= 1:
        return GuaranteeLevel.VERIFIED_MINIMAL

    state = _XPlainState(
        start_time=time.time(),
        max_solves=len(conflict_set) + 2,
        time_budget_sec=per_solve_sec * (len(conflict_set) + 2),
        per_solve_sec=per_solve_sec,
    )

    # 전체가 infeasible인지 확인
    if not _is_infeasible(conflict_set, math_model, bound_data, state):
        logger.warning("L5:presolve:validation_failed — conflict_set이 feasible")
        return GuaranteeLevel.APPROXIMATE

    # 하나씩 빼서 feasible인지 확인
    for c in conflict_set:
        reduced = [x for x in conflict_set if x != c]
        if _is_infeasible(reduced, math_model, bound_data, state):
            # c를 빼도 infeasible → c는 불필요 → minimal 아님
            logger.info(f"L5:presolve:not_minimal — {c} 제거해도 infeasible")
            return GuaranteeLevel.APPROXIMATE

    return GuaranteeLevel.VERIFIED_MINIMAL


# ── Soft 제거 테스트 ─────────────────────────────────────────

def test_soft_removal(
    math_model: Dict,
    bound_data: Dict,
    per_solve_sec: float = 3.0,
) -> SoftTestResult:
    """
    soft 제약 전체 제거 시 feasible 여부 테스트 (참고용).

    주의: soft ≠ optional. 결과는 원인 확정이 아닌 참고용.
    """
    hard_only = [
        c.get("name", c.get("id", ""))
        for c in math_model.get("constraints", [])
        if c.get("category", c.get("priority", "hard")) != "soft"
    ]

    if not hard_only:
        return SoftTestResult(soft_only_feasible=True)

    state = _XPlainState(
        start_time=time.time(),
        max_solves=1,
        time_budget_sec=per_solve_sec,
        per_solve_sec=per_solve_sec,
    )

    is_inf = _is_infeasible(hard_only, math_model, bound_data, state)

    return SoftTestResult(
        soft_only_feasible=not is_inf,
        note=(
            "soft 제거 결과는 참고용이며, soft ≠ optional입니다. "
            "soft_critical 제약이 포함되어 있을 수 있습니다."
        ),
    )


# ── Conflict Pair 자동 추출 ──────────────────────────────────

def extract_conflict_pairs(
    conflict_set: List[str],
    math_model: Dict,
) -> List[Dict[str, Any]]:
    """
    충돌 집합 내 제약 쌍의 변수 공유 관계를 추출.

    변수를 많이 공유하는 쌍 → 충돌 가능성 높음.
    """
    # 각 제약이 참조하는 변수 집합 구축
    constraint_vars: Dict[str, Set[str]] = {}
    for cdef in math_model.get("constraints", []):
        cname = cdef.get("name", cdef.get("id", ""))
        if cname not in conflict_set:
            continue
        # expression에서 변수 참조 추출 (간단 휴리스틱)
        variables_used: Set[str] = set()
        expr = cdef.get("expression", "")
        for var_def in math_model.get("variables", []):
            vid = var_def.get("id", "")
            if vid and vid in expr:
                variables_used.add(vid)
        # lhs/rhs에서도 추출
        for side in ["lhs", "rhs"]:
            node = cdef.get(side, {})
            if isinstance(node, dict):
                vid = node.get("variable", node.get("var", ""))
                if vid:
                    variables_used.add(vid)
        constraint_vars[cname] = variables_used

    # 쌍별 공유 변수 계산
    pairs = []
    conflict_list = list(conflict_set)
    for i, c1 in enumerate(conflict_list):
        for c2 in conflict_list[i + 1:]:
            shared = constraint_vars.get(c1, set()) & constraint_vars.get(c2, set())
            if shared:
                pairs.append({
                    "constraint_a": c1,
                    "constraint_b": c2,
                    "shared_variables": sorted(shared),
                    "overlap_count": len(shared),
                })

    return sorted(pairs, key=lambda p: p["overlap_count"], reverse=True)


# ── Confidence 계산 ──────────────────────────────────────────

def calculate_confidence(
    constraint_name: str,
    conflict_set: List[str],
    conflict_pairs: List[Dict],
    solver_stats: Optional[Dict] = None,
) -> float:
    """
    해당 제약이 실제 충돌 원인일 확률 추정.

    요소:
      1. conflict_set 포함 여부 (0.4 weight)
      2. Variable overlap (0.3 weight) — 공유 변수가 많을수록 높음
      3. Solver stats (0.3 weight) — 향후 ML 튜닝 대비
    """
    # 1. conflict_set 포함 여부
    freq_score = 1.0 if constraint_name in conflict_set else 0.0

    # 2. Variable overlap
    overlap_score = 0.0
    total_overlaps = 0
    for pair in conflict_pairs:
        if constraint_name in (pair["constraint_a"], pair["constraint_b"]):
            total_overlaps += pair["overlap_count"]
    if total_overlaps > 0:
        overlap_score = min(total_overlaps / 5.0, 1.0)  # 5개 이상 공유 → 1.0

    # 3. Solver stats (현재는 기본값, 향후 데이터 기반 조정)
    stats_score = 0.5  # 기본 중간값

    confidence = 0.4 * freq_score + 0.3 * overlap_score + 0.3 * stats_score
    return round(min(confidence, 1.0), 3)
