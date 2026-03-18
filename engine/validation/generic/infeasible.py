"""
Stage 6 — INFEASIBLE 진단 엔진 (플랫폼 공통).

솔버가 INFEASIBLE을 반환했을 때, 모델을 분석하여
어떤 제약이 충돌을 일으키는지 원인을 추정합니다.

진단 전략:
  1. 제약 그룹화: 어떤 제약 범주가 존재하는지 식별
  2. Executor 진단 정보(conflict_hints) 통합
  3. 시간 프레임 충돌 감지 (새벽 트립 vs 주야간 제약)
  4. 파라미터 긴축도: 바운드에 근접한 파라미터 탐색
  5. 완화 제안: 하드→소프트 전환 또는 파라미터 완화 제안
  6. 데이터 커버리지: 모든 제약을 충족할 데이터가 충분한지 검사

Context keys expected:
    status: str                — must be "INFEASIBLE" or "INFEASIBLE_BEST"
    math_model: dict           — the math model that was solved
    compile_summary: dict      — compile metadata
    domain: str                — domain identifier
    parameters: dict           — optional, for tightness analysis
    infeasibility_info: dict   — optional, executor-generated diagnosis
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from engine.validation.base import AutoFix, BaseValidator, UserInput, ValidationResult

KNOWLEDGE_BASE = Path(__file__).resolve().parents[3] / "knowledge" / "domains"


class InfeasibilityDiagnosisValidator(BaseValidator):
    """Diagnoses INFEASIBLE results with actionable suggestions."""

    stage = 6
    name = "InfeasibilityDiagnosisValidator"
    description = "비실현성 진단 및 완화 제안"

    def validate(self, context: dict) -> ValidationResult:
        result = self._make_result()
        status = context.get("status", "").upper()

        if status not in ("INFEASIBLE", "INFEASIBLE_BEST"):
            return result

        math_model = context.get("math_model", {})
        compile_summary = context.get("compile_summary", {})
        domain = context.get("domain", "")
        infeasibility_info = context.get("infeasibility_info") or {}

        constraints = math_model.get("constraints", [])
        if not constraints:
            result.add_error(
                code="INFEASIBLE_NO_MODEL",
                message="INFEASIBLE 상태이나 모델 정보가 없어 진단할 수 없습니다.",
            )
            return result

        # ── 1. Constraint category analysis ──
        hard_constraints = []
        soft_constraints = []
        for c in constraints:
            cat = c.get("priority", c.get("category", "hard"))
            if cat == "hard":
                hard_constraints.append(c)
            else:
                soft_constraints.append(c)

        status_label = "INFEASIBLE_BEST (실현 가능한 해 없음)" if status == "INFEASIBLE_BEST" else "INFEASIBLE"
        result.add_error(
            code="INFEASIBLE_DIAGNOSIS",
            message=(
                f"{status_label}: {len(hard_constraints)}개 하드 제약 중 일부가 "
                f"동시에 충족될 수 없습니다."
            ),
            detail=(
                f"하드 제약: {len(hard_constraints)}개, "
                f"소프트 제약: {len(soft_constraints)}개"
            ),
            context={
                "hard_count": len(hard_constraints),
                "soft_count": len(soft_constraints),
            },
        )

        # ── 2. Executor conflict hints (CP-SAT / D-Wave 진단 통합) ──
        conflict_hints = infeasibility_info.get("conflict_hints", [])
        solver_stats = infeasibility_info.get("solver_stats", {})

        for hint in conflict_hints:
            hint_type = hint.get("type", "")
            hint_msg = hint.get("message", "")
            hint_constraints = hint.get("constraints", [])

            if hint_type == "trivial_infeasibility":
                result.add_error(
                    code="INFEASIBLE_TRIVIAL",
                    message=hint_msg or "솔버가 탐색 없이 즉시 INFEASIBLE을 판정했습니다.",
                    detail="제약조건 값에 명백한 모순이 있습니다. presolve에서 바로 감지되었습니다.",
                )
            elif hint_type == "numeric_conflict":
                result.add_warning(
                    code="INFEASIBLE_NUMERIC_CONFLICT",
                    message=hint_msg,
                    detail=f"관련 제약: {', '.join(hint_constraints)}" if hint_constraints else None,
                    suggestion="인원수/총량 관련 파라미터 값이 서로 모순되지 않는지 확인하세요.",
                )
            elif hint_type == "coverage_capacity_conflict":
                result.add_warning(
                    code="INFEASIBLE_COVERAGE_CAPACITY",
                    message=hint_msg,
                    detail=f"관련 제약: {', '.join(hint_constraints)}" if hint_constraints else None,
                    suggestion="자원(근무조 수)이 모든 할당 의무를 충족하기에 충분한지 확인하세요.",
                )
            else:
                result.add_info(
                    code=f"INFEASIBLE_HINT_{hint_type.upper()}",
                    message=hint_msg,
                )

        if solver_stats.get("conflicts", -1) == 0:
            result.add_info(
                code="INFEASIBLE_PRESOLVE_DETECTED",
                message="솔버 conflicts=0: 탐색 전 presolve 단계에서 비실현성이 감지되었습니다.",
            )

        # ── 3. Time frame conflict detection (새벽 트립 vs 주야간 제약) ──
        self._check_time_frame_conflict(context, result, hard_constraints)

        # ── 4. Identify tight constraint groups ──
        time_constraints = []
        resource_constraints = []
        coverage_constraints = []
        other_constraints = []

        for c in hard_constraints:
            cid = (c.get("id") or c.get("name", "")).lower()
            desc = (c.get("description") or "").lower()
            combined = f"{cid} {desc}"

            if any(k in combined for k in ("time", "시간", "duration", "분", "minute")):
                time_constraints.append(c)
            elif any(k in combined for k in ("cover", "커버", "assign", "배정", "trip")):
                coverage_constraints.append(c)
            elif any(k in combined for k in ("resource", "count", "수", "limit", "상한")):
                resource_constraints.append(c)
            else:
                other_constraints.append(c)

        # ── 5. Suggest relaxation strategies ──
        if time_constraints and coverage_constraints:
            time_names = ", ".join(
                c.get("id", c.get("name", "?")) for c in time_constraints[:3]
            )
            result.add_warning(
                code="INFEASIBLE_TIME_COVERAGE_CONFLICT",
                message=(
                    f"시간 제약({time_names})과 커버리지 제약이 충돌할 가능성이 높습니다."
                ),
                suggestion=(
                    "시간 제한을 완화하거나 커버리지 제약을 소프트로 전환해 보세요."
                ),
            )

        # ── 6. Suggest specific hard→soft conversions ──
        relaxable = [
            c for c in hard_constraints
            if not c.get("fixed_category", False)
        ]
        if relaxable:
            suggestions = []
            for c in relaxable[:5]:
                cid = c.get("id", c.get("name", ""))
                desc = c.get("description", "")
                suggestions.append(f"{cid} ({desc})" if desc else cid)

            result.add_warning(
                code="INFEASIBLE_RELAX_SUGGESTION",
                message=f"소프트 전환 가능한 하드 제약 {len(relaxable)}개:",
                detail="\n".join(f"  - {s}" for s in suggestions),
                suggestion="일부 제약을 소프트로 전환하면 실행 가능한 해를 찾을 수 있습니다.",
            )

        # ── 7. Parameter tightness check ──
        parameters = context.get("parameters", {})
        # math_model.parameters는 list 형태일 수 있음 → dict 변환
        if isinstance(parameters, list):
            parameters = {
                p.get("id", ""): p.get("default_value") or p.get("value")
                for p in parameters if p.get("id")
            }
        if parameters and domain:
            tight_params = self._check_parameter_tightness(parameters, domain)
            if tight_params:
                param_list = ", ".join(tight_params[:5])
                result.add_warning(
                    code="INFEASIBLE_TIGHT_PARAMETERS",
                    message=f"범위 하한/상한에 가까운 파라미터: {param_list}",
                    suggestion="해당 파라미터를 완화하면 실행 가능성이 높아질 수 있습니다.",
                    context={"tight_params": tight_params},
                )

        # ── 8. Data sufficiency check ──
        compile_constraints = compile_summary.get("constraints", {})
        failed = compile_constraints.get("failed", 0)
        if failed > 0:
            result.add_info(
                code="INFEASIBLE_COMPILE_FAILURES",
                message=f"컴파일 시 {failed}개 제약이 실패했습니다. 이것이 비실현성과 관련될 수 있습니다.",
            )

        return result

    def _check_time_frame_conflict(
        self, context: dict, result: ValidationResult, hard_constraints: list
    ) -> None:
        """Canonical axis 기반 시간 프레임 충돌 검증."""
        policy_result = context.get("_policy_result")
        compile_summary = context.get("compile_summary", {})

        if policy_result:
            # Policy 적용됨 → canonical axis 기준 검증
            canonical = policy_result.get("canonical", {})
            provenance = policy_result.get("provenance", [])
            shifted_count = sum(1 for p in provenance if p.get("reason") == "shift_if_before_anchor")

            if shifted_count > 0:
                result.add_info(
                    code="POLICY_TIME_NORMALIZATION_APPLIED",
                    message=(
                        f"PolicyEngine: {shifted_count}개 새벽 트립이 서비스데이 축으로 "
                        f"정규화되었습니다 (shift_if_before_anchor)."
                    ),
                )
        else:
            # Policy 미적용 → 기존 raw 기반 경고
            parameters = context.get("parameters", {})
            day_start = None
            is_overnight = None

            if isinstance(parameters, dict):
                ds = parameters.get("day_duty_start_earliest")
                day_start = ds.get("value") if isinstance(ds, dict) else ds
                ov = parameters.get("is_overnight_crew")
                is_overnight = ov.get("value") if isinstance(ov, dict) else ov

            if day_start is not None:
                try:
                    ds_min = float(day_start)
                except (ValueError, TypeError):
                    return

                if ds_min > 300:
                    gap_hours = f"{int(ds_min) // 60:02d}:{int(ds_min) % 60:02d}"
                    if is_overnight is True:
                        result.add_error(
                            code="INFEASIBLE_OVERNIGHT_NO_POLICY",
                            message=(
                                f"숙박조(is_overnight_crew=True)이지만 PolicyEngine이 "
                                f"적용되지 않았습니다. 새벽 트립 시간 정규화가 필요합니다."
                            ),
                            suggestion="policies.yaml에 TimeAxisPolicy가 정의되어 있는지 확인하세요.",
                        )
                    else:
                        result.add_warning(
                            code="INFEASIBLE_EARLY_MORNING_GAP",
                            message=(
                                f"새벽 시간대(00:00~{gap_hours}) 트립 배정 불가 가능성"
                            ),
                            suggestion="숙박조(is_overnight_crew) 설정이 필요할 수 있습니다.",
                        )

    def _check_parameter_tightness(
        self, parameters: dict, domain: str
    ) -> list[str]:
        """Find parameters that are at or near their reference range extremes."""
        ref_path = KNOWLEDGE_BASE / domain / "reference_ranges.yaml"
        if not ref_path.exists():
            return []

        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                ref_data = yaml.safe_load(f) or {}
        except Exception:
            return []

        # Build min/max from all sub-domains
        bounds: dict[str, tuple[float, float]] = {}
        for sub in ref_data.values():
            for param, val in sub.get("values", {}).items():
                try:
                    v = float(val)
                except (ValueError, TypeError):
                    continue
                if param not in bounds:
                    bounds[param] = (v, v)
                else:
                    lo, hi = bounds[param]
                    bounds[param] = (min(lo, v), max(hi, v))

        tight = []
        for param_id, param_val in parameters.items():
            val = param_val.get("value") if isinstance(param_val, dict) else param_val
            if val is None or param_id not in bounds:
                continue
            try:
                v = float(val)
            except (ValueError, TypeError):
                continue

            lo, hi = bounds[param_id]
            rng = hi - lo if hi != lo else abs(hi) * 0.1 or 1
            if v <= lo or v >= hi:
                tight.append(param_id)
            elif (v - lo) / rng < 0.1 or (hi - v) / rng < 0.1:
                tight.append(param_id)

        return tight
