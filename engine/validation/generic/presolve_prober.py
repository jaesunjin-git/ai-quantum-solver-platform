"""
presolve_prober.py ──────────────────────────────────────────
Stage 5 Presolve Feasibility Probing — 메인 오케스트레이터.

솔버 실행 전 Canonical Model 기준으로 feasibility를 사전 판정하여:
  - INFEASIBLE → D-Wave 등 고비용 솔버 실행 차단 + 충돌 진단
  - FEASIBLE → 솔버 실행 허용
  - TIMEOUT → risk 평가 + 사용자 확인 요청

5단계 파이프라인:
  Step 1: Cache Check (hash + version)
  Step 2: Pre-check (자명한 infeasibility, 0.01초)
  Step 3: Canonical → CP-SAT 변환 (손실 리포트)
  Step 4: Quick Solve (5~10초, worker=1)
  Step 5: Conflict Detection (INFEASIBLE 시만, QuickXPlain)

Fidelity Enforcement Policy (코드로 강제):
  - fidelity < 0.7  → INFEASIBLE이어도 warn_only (hard block 금지)
  - 0.7 ≤ f < 0.9   → conditional_block (사용자 확인)
  - fidelity ≥ 0.9  → hard_block 허용
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from engine.validation.base import BaseValidator, ValidationResult
from engine.validation.generic.presolve_models import (
    PresolveConfig,
    PresolveResult,
    PresolveStatus,
    FidelityDecision,
    TrivialConflict,
    QuickSolveResult,
    TimeoutAssessment,
    ConflictDiagnosis,
    ConflictEntry,
    GuaranteeLevel,
    build_cache_key,
)

logger = logging.getLogger(__name__)

# ── 모듈 레벨 캐시 ──────────────────────────────────────────
_presolve_cache: Dict[str, PresolveResult] = {}
_CACHE_MAX_SIZE = 50


class PresolveProber(BaseValidator):
    """
    Stage 5 Presolve Feasibility Probing 검증기.

    BaseValidator를 상속하여 ValidationRegistry에 자동 등록 가능.
    validate() 호출 시 5단계 파이프라인을 실행하고
    ValidationResult + PresolveResult를 반환한다.
    """

    stage = 5
    name = "PresolveProber"
    description = "솔버 실행 전 CP-SAT 기반 feasibility 사전 판정"

    def __init__(self, config: Optional[PresolveConfig] = None):
        self.config = config or PresolveConfig.from_env()

    def validate(self, context: dict) -> ValidationResult:
        """
        Stage 5 context에서 presolve probing 실행.

        context 필수 키:
          - math_model: Dict — 수학 모델 IR
          - compile_summary: Dict — 컴파일 요약
          - model_stats: Dict — {total_variables, total_constraints}

        context 선택 키:
          - bound_data: Dict — DataBinder 결과 (없으면 축소 실행)
          - gate3_result: Dict — Gate3 검증 결과
          - policy_version: str
          - catalog_version: str
        """
        result = self._make_result()
        math_model = context.get("math_model", {})
        bound_data = context.get("bound_data", {})
        gate3_result = context.get("gate3_result", {})

        # SP 모델에서는 presolve 불필요 (시간 제약 없음)
        compile_summary = context.get("compile_summary", {})
        if compile_summary.get("model_type") == "SetPartitioning":
            result.add_info(
                code="PRESOLVE_SKIPPED_SP",
                message="Set Partitioning 모델 — presolve 불필요 (시간 제약은 Generator에서 검증됨)",
            )
            return result

        # bound_data가 없으면 presolve 실행 불가
        if not bound_data or not bound_data.get("sets"):
            result.add_info(
                code="PRESOLVE_SKIPPED",
                message="데이터 바인딩 정보가 없어 presolve를 건너뜁니다.",
            )
            return result

        # 제약 수 확인 — skip 조건
        constraint_count = len(math_model.get("constraints", []))
        if constraint_count < self.config.skip_threshold_constraints:
            result.add_info(
                code="PRESOLVE_SKIPPED_SMALL",
                message=f"제약 {constraint_count}개 — 소규모 모델로 presolve를 건너뜁니다.",
                context={"constraint_count": constraint_count,
                         "threshold": self.config.skip_threshold_constraints},
            )
            return result

        # Presolve 실행
        request_id = str(uuid.uuid4())[:8]
        presolve_result = self._run_presolve(
            math_model=math_model,
            bound_data=bound_data,
            gate3_result=gate3_result,
            policy_version=context.get("policy_version", ""),
            catalog_version=context.get("catalog_version", ""),
            request_id=request_id,
        )

        # 결과를 context에 저장 (pipeline에서 참조)
        context["presolve_result"] = presolve_result

        # ValidationResult에 반영
        self._populate_validation_result(result, presolve_result)

        # Observability 로깅
        self._log_result(presolve_result)

        return result

    # ── 5단계 파이프라인 ────────────────────────────────────

    def _run_presolve(
        self,
        math_model: Dict,
        bound_data: Dict,
        gate3_result: Dict,
        policy_version: str,
        catalog_version: str,
        request_id: str,
    ) -> PresolveResult:
        """5단계 presolve 파이프라인 실행"""
        start = time.time()

        # ── Step 1: Cache Check ──
        cache_key = build_cache_key(math_model, bound_data, policy_version, catalog_version)
        if self.config.cache_enabled and cache_key in _presolve_cache:
            cached = _presolve_cache[cache_key]
            return PresolveResult(
                status=cached.status,
                phase="cache_hit",
                elapsed_sec=0.0,
                request_id=request_id,
                decision=cached.decision,
                decision_message=cached.decision_message,
                build_report=cached.build_report,
                conflict_diagnosis=cached.conflict_diagnosis,
                cached=True,
                cache_key=cache_key,
            )

        # ── Step 2: Pre-check (자명한 infeasibility) ──
        trivial_issues = self._pre_check(math_model, bound_data)
        if trivial_issues:
            result = PresolveResult(
                status=PresolveStatus.TRIVIAL_INFEASIBLE,
                phase="pre_check",
                elapsed_sec=time.time() - start,
                request_id=request_id,
                decision=FidelityDecision.HARD_BLOCK,
                decision_message="수리적으로 자명한 infeasibility가 감지되었습니다.",
                trivial_issues=trivial_issues,
                cache_key=cache_key,
            )
            self._cache_put(cache_key, result)
            return result

        # ── Step 3: Canonical → CP-SAT 변환 ──
        try:
            from engine.validation.generic.canonical_cpsat_builder import (
                build_cpsat_for_presolve,
            )
            probe_model, build_report = build_cpsat_for_presolve(math_model, bound_data)
        except Exception as e:
            logger.warning(f"L5:presolve:build_failed error={e}")
            return PresolveResult(
                status=PresolveStatus.SKIPPED,
                phase="build_failed",
                elapsed_sec=time.time() - start,
                request_id=request_id,
                decision=FidelityDecision.PROCEED,
                decision_message=f"CP-SAT 모델 생성 실패: {e}",
                cache_key=cache_key,
            )

        # fidelity가 너무 낮으면 quick solve 의미 없음
        if build_report.fidelity_score < 0.5:
            return PresolveResult(
                status=PresolveStatus.SKIPPED,
                phase="low_fidelity",
                elapsed_sec=time.time() - start,
                request_id=request_id,
                decision=FidelityDecision.PROCEED,
                decision_message=(
                    f"변환 충실도 {build_report.fidelity_score:.0%}로 "
                    f"presolve 의미가 낮아 건너뜁니다."
                ),
                build_report=build_report,
                cache_key=cache_key,
            )

        # ── Step 4: Quick Solve ──
        quick_result = self._quick_solve(probe_model, build_report)

        if quick_result.status == PresolveStatus.FEASIBLE:
            # FEASIBLE — 솔버 실행 허용 (단 dropped 제약 경고)
            decision = FidelityDecision.PROCEED
            msg = "Presolve FEASIBLE — 솔버 실행을 진행합니다."
            if build_report.dropped_constraints:
                msg += (
                    f" (단, {len(build_report.dropped_constraints)}개 제약이 "
                    f"presolve에서 제외되어 실제 결과와 다를 수 있습니다)"
                )

            result = PresolveResult(
                status=PresolveStatus.FEASIBLE,
                phase="quick_solve",
                elapsed_sec=time.time() - start,
                request_id=request_id,
                decision=decision,
                decision_message=msg,
                build_report=build_report,
                quick_solve=quick_result,
                cache_key=cache_key,
            )
            self._cache_put(cache_key, result)
            return result

        if quick_result.status == PresolveStatus.TIMEOUT:
            # TIMEOUT — risk 평가
            assessment = self._assess_timeout_risk(
                math_model, gate3_result, build_report
            )
            decision = (
                FidelityDecision.USER_CONFIRMATION
                if assessment.risk_score >= self.config.risk_high_threshold
                else FidelityDecision.PROCEED
            )

            result = PresolveResult(
                status=PresolveStatus.TIMEOUT,
                phase="quick_solve",
                elapsed_sec=time.time() - start,
                request_id=request_id,
                decision=decision,
                decision_message=assessment.message,
                build_report=build_report,
                quick_solve=quick_result,
                timeout_assessment=assessment,
                cache_key=cache_key,
            )
            self._cache_put(cache_key, result)
            return result

        # ── Step 5: INFEASIBLE → Conflict Detection ──
        # Fidelity Enforcement (코드로 강제)
        fidelity = build_report.fidelity_score
        if fidelity >= self.config.fidelity_high:
            decision = FidelityDecision.HARD_BLOCK
        elif fidelity >= self.config.fidelity_medium:
            decision = FidelityDecision.CONDITIONAL_BLOCK
        else:
            decision = FidelityDecision.WARN_ONLY

        # Conflict Detection
        diagnosis = self._detect_conflicts(math_model, bound_data, quick_result)

        msg = self._build_infeasible_message(decision, build_report, diagnosis)

        result = PresolveResult(
            status=PresolveStatus.INFEASIBLE,
            phase="conflict_detection",
            elapsed_sec=time.time() - start,
            request_id=request_id,
            decision=decision,
            decision_message=msg,
            build_report=build_report,
            quick_solve=quick_result,
            conflict_diagnosis=diagnosis,
            cache_key=cache_key,
        )
        self._cache_put(cache_key, result)
        return result

    # ── Step 2: Pre-check ───────────────────────────────────

    def _pre_check(self, math_model: Dict, bound_data: Dict) -> List[TrivialConflict]:
        """솔버 호출 0회, 수리적 자명 infeasibility 탐지"""
        issues: List[TrivialConflict] = []

        # 1. Variable domain empty (lb > ub)
        for var_def in math_model.get("variables", []):
            lb = var_def.get("lower_bound")
            ub = var_def.get("upper_bound")
            if lb is not None and ub is not None:
                try:
                    if float(lb) > float(ub):
                        issues.append(TrivialConflict(
                            code="EMPTY_DOMAIN",
                            message=(
                                f"변수 '{var_def.get('id')}': "
                                f"하한({lb}) > 상한({ub})으로 해가 존재할 수 없습니다."
                            ),
                            context={"variable": var_def.get("id"), "lb": lb, "ub": ub},
                        ))
                except (TypeError, ValueError):
                    pass

        # 2. 자원 부족: 가용 set 크기 vs 필수 커버리지
        sets = bound_data.get("sets", {})
        params = bound_data.get("parameters", {})

        # crew/resource set이 있고, trips/jobs set도 있을 때 비교
        for resource_key in ["crews", "drivers", "vehicles", "workers"]:
            if resource_key in sets:
                resource_count = len(sets[resource_key])
                for demand_key in ["trips", "jobs", "tasks", "orders"]:
                    if demand_key in sets:
                        demand_count = len(sets[demand_key])
                        # 각 자원이 최소 1개 job을 커버한다고 가정해도 부족하면
                        max_capacity = params.get("max_duties_per_crew", demand_count)
                        if isinstance(max_capacity, (int, float)) and resource_count * max_capacity < demand_count:
                            issues.append(TrivialConflict(
                                code="RESOURCE_SHORTAGE",
                                message=(
                                    f"가용 {resource_key}({resource_count}) × "
                                    f"최대 할당({max_capacity}) = {resource_count * max_capacity} < "
                                    f"필수 {demand_key}({demand_count})"
                                ),
                                context={
                                    "resource": resource_key,
                                    "resource_count": resource_count,
                                    "demand": demand_key,
                                    "demand_count": demand_count,
                                },
                            ))

        return issues

    # ── Step 4: Quick Solve ─────────────────────────────────

    def _quick_solve(self, probe_model: Any, build_report: Any) -> QuickSolveResult:
        """CP-SAT Quick Solve (최소 리소스)"""
        try:
            from ortools.sat.python import cp_model as cp_module

            solver = cp_module.CpSolver()
            solver.parameters.max_time_in_seconds = self.config.quick_solve_time_limit_sec
            solver.parameters.num_search_workers = self.config.quick_solve_num_workers
            solver.parameters.log_search_progress = False

            start = time.time()
            status = solver.solve(probe_model)
            elapsed = time.time() - start

            status_map = {
                cp_module.OPTIMAL: PresolveStatus.FEASIBLE,
                cp_module.FEASIBLE: PresolveStatus.FEASIBLE,
                cp_module.INFEASIBLE: PresolveStatus.INFEASIBLE,
                cp_module.MODEL_INVALID: PresolveStatus.INFEASIBLE,
                cp_module.UNKNOWN: PresolveStatus.TIMEOUT,
            }

            return QuickSolveResult(
                status=status_map.get(status, PresolveStatus.TIMEOUT),
                elapsed_sec=round(elapsed, 3),
                solver_stats={
                    "conflicts": solver.num_conflicts,
                    "branches": solver.num_branches,
                },
                fidelity_score=build_report.fidelity_score,
                dropped_constraints=build_report.dropped_constraints,
            )

        except Exception as e:
            logger.warning(f"L5:presolve:quick_solve_error error={e}")
            return QuickSolveResult(
                status=PresolveStatus.TIMEOUT,
                elapsed_sec=0.0,
            )

    # ── Step 4-b: Timeout Risk ──────────────────────────────

    def _assess_timeout_risk(
        self,
        math_model: Dict,
        gate3_result: Dict,
        build_report: Any,
    ) -> TimeoutAssessment:
        """TIMEOUT 시 risk score 산정"""
        risk_factors = []
        score = 0.0

        var_count = len(math_model.get("variables", []))
        con_count = len(math_model.get("constraints", []))

        if var_count > 50 or con_count > 100:
            score += 0.2
            risk_factors.append(f"모델 규모 (변수 {var_count}, 제약 {con_count})")

        stats = gate3_result.get("stats", {})
        if stats.get("constant_infeasible", 0) > 0:
            score += 0.4
            risk_factors.append(
                f"constant infeasible {stats['constant_infeasible']}건 감지"
            )
        if stats.get("hard_truncation_count", 0) > 0:
            score += 0.2
            risk_factors.append(
                f"hard truncation {stats['hard_truncation_count']}건"
            )

        if build_report and build_report.fidelity_score < 0.8:
            score += 0.2
            risk_factors.append(
                f"presolve 변환 손실 높음 (fidelity {build_report.fidelity_score:.0%})"
            )

        score = min(score, 1.0)
        action = (
            "user_confirmation"
            if score >= self.config.risk_high_threshold
            else "proceed_with_warning"
        )

        if action == "user_confirmation":
            message = (
                "Presolve 시간 초과 — 모델 복잡도가 높아 infeasible 가능성이 있습니다. "
                "솔버를 실행하시겠습니까?"
            )
        else:
            message = (
                "Presolve 시간 초과 (모델이 큼) — "
                "결과가 불확실할 수 있습니다."
            )

        return TimeoutAssessment(
            risk_score=score,
            risk_factors=risk_factors,
            action=action,
            message=message,
        )

    # ── Step 5: Conflict Detection ──────────────────────────

    def _detect_conflicts(
        self,
        math_model: Dict,
        bound_data: Dict,
        quick_result: QuickSolveResult,
    ) -> ConflictDiagnosis:
        """INFEASIBLE 시 QuickXPlain 기반 충돌 탐색"""
        from engine.validation.generic.quickxplain import (
            quickxplain,
            rank_constraints_by_risk,
            validate_conflict_set,
            test_soft_removal,
            extract_conflict_pairs,
            calculate_confidence,
        )

        all_names = [
            c.get("name", c.get("id", ""))
            for c in math_model.get("constraints", [])
        ]

        # 우선순위 정렬
        ranked = rank_constraints_by_risk(all_names, math_model)

        # QuickXPlain 실행
        conflict_set, guarantee, solve_count, max_depth = quickxplain(
            all_constraint_names=ranked,
            math_model=math_model,
            bound_data=bound_data,
            max_solves=self.config.max_solves,
            max_depth=self.config.max_depth,
            time_budget_sec=self.config.conflict_time_budget_sec,
            per_solve_sec=self.config.per_solve_limit_sec,
        )

        # 재검증 (시간 여유 있을 때만)
        if guarantee == GuaranteeLevel.MINIMAL and len(conflict_set) <= 10:
            guarantee = validate_conflict_set(
                conflict_set, math_model, bound_data,
                per_solve_sec=self.config.per_solve_limit_sec,
            )

        # Soft 제거 테스트 (참고용)
        soft_test = test_soft_removal(
            math_model, bound_data,
            per_solve_sec=self.config.per_solve_limit_sec,
        )

        # Conflict Pair 추출
        conflict_pairs = extract_conflict_pairs(conflict_set, math_model)

        # ConflictEntry 구성
        conflict_entries = []
        constraint_map = {
            c.get("name", c.get("id", "")): c
            for c in math_model.get("constraints", [])
        }
        for cname in conflict_set:
            cdef = constraint_map.get(cname, {})
            conf = calculate_confidence(
                cname, conflict_set, conflict_pairs, quick_result.solver_stats
            )
            # conflict pair 찾기
            pair = None
            for p in conflict_pairs:
                if cname in (p["constraint_a"], p["constraint_b"]):
                    pair = (
                        p["constraint_b"]
                        if p["constraint_a"] == cname
                        else p["constraint_a"]
                    )
                    break

            conflict_entries.append(ConflictEntry(
                constraint=cname,
                type=cdef.get("category", cdef.get("priority", "hard")),
                confidence=conf,
                overlapping_variables=[
                    v for p in conflict_pairs
                    if cname in (p["constraint_a"], p["constraint_b"])
                    for v in p["shared_variables"]
                ][:5],  # 상위 5개만
                conflict_pair=pair,
                reason=self._build_conflict_reason(cname, pair, conflict_pairs),
            ))

        return ConflictDiagnosis(
            conflict_candidate_set=conflict_set,
            guarantee_level=guarantee,
            conflicts=conflict_entries,
            soft_test=soft_test,
            conflict_pairs=conflict_pairs,
            solve_count=solve_count,
            max_depth_reached=max_depth,
            total_elapsed_sec=0.0,  # 상위에서 계산
        )

    # ── Helper ──────────────────────────────────────────────

    def _build_conflict_reason(
        self,
        constraint: str,
        pair: Optional[str],
        conflict_pairs: List[Dict],
    ) -> str:
        """충돌 이유 자동 생성"""
        if pair:
            shared = []
            for p in conflict_pairs:
                if constraint in (p["constraint_a"], p["constraint_b"]) and \
                   pair in (p["constraint_a"], p["constraint_b"]):
                    shared = p.get("shared_variables", [])
                    break
            if shared:
                return f"{pair}와 변수 {', '.join(shared[:3])}을 공유하며 충돌"
            return f"{pair}와 충돌"
        return "충돌 집합에 포함됨"

    def _build_infeasible_message(
        self,
        decision: FidelityDecision,
        build_report: Any,
        diagnosis: ConflictDiagnosis,
    ) -> str:
        """INFEASIBLE 판정 사용자 메시지 생성"""
        conflict_names = ", ".join(diagnosis.conflict_candidate_set[:5])
        base = f"Presolve INFEASIBLE — 충돌 제약: {conflict_names}"

        if len(diagnosis.conflict_candidate_set) > 5:
            base += f" 외 {len(diagnosis.conflict_candidate_set) - 5}개"

        if decision == FidelityDecision.HARD_BLOCK:
            return base + ". 솔버 실행을 차단합니다."
        elif decision == FidelityDecision.CONDITIONAL_BLOCK:
            return base + ". 솔버 실행 전 확인이 필요합니다."
        else:
            return (
                base + ". (참고: presolve 변환 충실도가 낮아 "
                "실제 결과와 다를 수 있습니다)"
            )

    def _populate_validation_result(
        self,
        result: ValidationResult,
        presolve: PresolveResult,
    ) -> None:
        """PresolveResult를 ValidationResult에 반영"""
        ctx = presolve.to_dict()

        if presolve.status in (
            PresolveStatus.INFEASIBLE,
            PresolveStatus.TRIVIAL_INFEASIBLE,
        ):
            if presolve.decision == FidelityDecision.HARD_BLOCK:
                result.add_error(
                    code="PRESOLVE_INFEASIBLE",
                    message=presolve.decision_message,
                    context=ctx,
                )
            elif presolve.decision == FidelityDecision.CONDITIONAL_BLOCK:
                result.add_warning(
                    code="PRESOLVE_INFEASIBLE_CONDITIONAL",
                    message=presolve.decision_message,
                    context=ctx,
                )
            else:
                result.add_warning(
                    code="PRESOLVE_INFEASIBLE_LOWFIDELITY",
                    message=presolve.decision_message,
                    context=ctx,
                )

        elif presolve.status == PresolveStatus.TIMEOUT:
            if presolve.decision == FidelityDecision.USER_CONFIRMATION:
                result.add_warning(
                    code="PRESOLVE_TIMEOUT_HIGH_RISK",
                    message=presolve.decision_message,
                    context=ctx,
                )
            else:
                result.add_info(
                    code="PRESOLVE_TIMEOUT",
                    message=presolve.decision_message,
                    context=ctx,
                )

        elif presolve.status == PresolveStatus.FEASIBLE:
            msg = "Presolve FEASIBLE — 솔버 실행을 진행합니다."
            if presolve.build_report and presolve.build_report.dropped_constraints:
                msg += (
                    f" ({len(presolve.build_report.dropped_constraints)}개 제약 "
                    f"presolve 제외, 실제 결과 상이 가능)"
                )
            result.add_info(
                code="PRESOLVE_FEASIBLE",
                message=msg,
                context=ctx,
            )

    def _log_result(self, presolve: PresolveResult) -> None:
        """Observability 구조화 로깅 (replay 지원)"""
        logger.info(
            "L5:presolve:complete",
            extra={
                "request_id": presolve.request_id,
                "status": presolve.status.value,
                "phase": presolve.phase,
                "elapsed_sec": round(presolve.elapsed_sec, 3),
                "decision": presolve.decision.value,
                "fidelity_score": (
                    presolve.build_report.fidelity_score
                    if presolve.build_report else None
                ),
                "conflict_count": (
                    len(presolve.conflict_diagnosis.conflict_candidate_set)
                    if presolve.conflict_diagnosis else 0
                ),
                "cached": presolve.cached,
                "cache_key": presolve.cache_key,
            },
        )

    def _cache_put(self, key: str, result: PresolveResult) -> None:
        """캐시 저장 (LRU 방식)"""
        if not self.config.cache_enabled:
            return
        if len(_presolve_cache) >= _CACHE_MAX_SIZE:
            # 가장 오래된 항목 제거
            oldest = next(iter(_presolve_cache))
            del _presolve_cache[oldest]
        _presolve_cache[key] = result
