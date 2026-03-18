"""
Stage 6 — INFEASIBLE 진단 엔진 (플랫폼 공통).

솔버가 INFEASIBLE을 반환했을 때, 모델을 분석하여
어떤 제약이 충돌을 일으키는지 원인을 추정합니다.

진단 전략:
  1. 제약 그룹화: 어떤 제약 범주가 존재하는지 식별
  2. 파라미터 긴축도: 바운드에 근접한 파라미터 탐색
  3. 완화 제안: 하드→소프트 전환 또는 파라미터 완화 제안
  4. 데이터 커버리지: 모든 제약을 충족할 데이터가 충분한지 검사

Context keys expected:
    status: str                — must be "INFEASIBLE" to trigger analysis
    math_model: dict           — the math model that was solved
    compile_summary: dict      — compile metadata
    domain: str                — domain identifier
    parameters: dict           — optional, for tightness analysis
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

        result.add_error(
            code="INFEASIBLE_DIAGNOSIS",
            message=(
                f"INFEASIBLE: {len(hard_constraints)}개 하드 제약 중 일부가 "
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

        # ── 2. Identify tight constraint groups ──
        # Group constraints by their likely conflict patterns
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

        # ── 3. Suggest relaxation strategies ──
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

        # ── 4. Suggest specific hard→soft conversions ──
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

        # ── 5. Parameter tightness check ──
        parameters = context.get("parameters", {})
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

        # ── 6. Data sufficiency check ──
        compile_constraints = compile_summary.get("constraints", {})
        failed = compile_constraints.get("failed", 0)
        if failed > 0:
            result.add_info(
                code="INFEASIBLE_COMPILE_FAILURES",
                message=f"컴파일 시 {failed}개 제약이 실패했습니다. 이것이 비실현성과 관련될 수 있습니다.",
            )

        return result

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
