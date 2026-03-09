"""
engine/gates/gate3_compile_check.py
───────────────────────────────────
Gate 3: 컴파일 결과 검증

컴파일 후, 솔버 실행 전에 결과를 검증한다.
문제점을 사전에 감지하여 불필요한 솔버 실행을 방지한다.

검증 항목:
  1. 제약 적용률 — 전체 제약 중 실제 적용된 비율
  2. 변수 수 정합성 — Gate 2 예측과 실제 생성 변수 수 비교
  3. 파라미터 바인딩 실패 — None으로 남은 파라미터 감지
  4. 명백한 비실현성 감지 — 변수 0개, 제약 0개 등
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run(compile_result: Dict,
        math_model: Optional[Dict] = None,
        gate2_result: Optional[Dict] = None) -> Dict[str, Any]:
    """
    메인 검증 함수.

    Args:
        compile_result: 컴파일러가 반환한 결과
            - variable_count: 생성된 변수 수
            - constraint_count: 적용된 제약 수
            - warnings: 컴파일 경고 목록
            - compile_time: 컴파일 시간
        math_model: 원본 수학 모델 (제약 수 비교용)
        gate2_result: Gate 2 결과 (변수 수 비교용)

    Returns:
        {
            "pass": bool,           # 실행 진행 가능 여부
            "errors": [...],        # 치명적 (실행 불가)
            "warnings": [...],      # 주의 (실행 가능하지만 결과 품질 저하 가능)
            "stats": {...},         # 통계 정보
        }
    """
    errors: List[str] = []
    warnings: List[str] = []
    stats: Dict[str, Any] = {}

    actual_vars = compile_result.get("variable_count", 0)
    actual_constraints = compile_result.get("constraint_count", 0)
    compile_warnings = compile_result.get("warnings", [])
    compile_time = compile_result.get("compile_time", 0)

    stats["actual_variables"] = actual_vars
    stats["actual_constraints"] = actual_constraints
    stats["compile_time"] = compile_time
    stats["compile_warnings"] = len(compile_warnings)

    # ── 1. 기본 유효성 ──
    if actual_vars == 0:
        errors.append("변수가 0개 생성됨 — 모델 정의 오류")

    if actual_constraints == 0:
        errors.append("제약이 0개 적용됨 — 모든 제약 파싱 실패")

    # ── 2. 제약 적용률 ──
    if math_model:
        model_constraints = math_model.get("constraints", [])
        hard_constraints = [c for c in model_constraints
                           if c.get("priority", c.get("category", "hard")) == "hard"]
        soft_constraints = [c for c in model_constraints
                           if c.get("priority", c.get("category", "")) == "soft"]
        total_defined = len(model_constraints)
        hard_defined = len(hard_constraints)
        soft_defined = len(soft_constraints)

        stats["defined_constraints"] = total_defined
        stats["hard_defined"] = hard_defined
        stats["soft_defined"] = soft_defined

        # 컴파일 경고에서 실패한 제약 수 추정
        failed_constraints = 0
        skipped_soft = 0
        for w in compile_warnings:
            w_str = str(w).lower()
            # "structured build returned 0" 은 fallback 시도 경고 → 최종 실패 아님
            # "all parse methods failed" 만 3단계 모두 실패한 진짜 최종 실패
            if "all parse methods failed" in w_str:
                failed_constraints += 1
            if "soft" in w_str and "skip" in w_str:
                skipped_soft += 1

        stats["failed_constraints"] = failed_constraints
        stats["skipped_soft"] = skipped_soft

        # hard 제약 적용률
        hard_applied = hard_defined - failed_constraints
        if hard_defined > 0:
            hard_ratio = hard_applied / hard_defined
            stats["hard_apply_ratio"] = round(hard_ratio, 2)

            if hard_ratio < 0.5:
                errors.append(
                    f"hard 제약 적용률 {hard_ratio:.0%} ({hard_applied}/{hard_defined}) — "
                    f"50% 미만이면 솔버 결과 신뢰 불가"
                )
            elif hard_ratio < 0.8:
                warnings.append(
                    f"hard 제약 적용률 {hard_ratio:.0%} ({hard_applied}/{hard_defined}) — "
                    f"일부 제약 누락으로 결과가 부정확할 수 있음"
                )

    # ── 3. 변수 수 정합성 ──
    if gate2_result:
        expected_vars = gate2_result.get("actual_variable_count", 0)
        if expected_vars > 0 and actual_vars > 0:
            ratio = abs(actual_vars - expected_vars) / max(expected_vars, 1)
            stats["variable_match_ratio"] = round(1 - ratio, 2)

            if ratio > 0.5:
                warnings.append(
                    f"변수 수 불일치: Gate 2 예측 {expected_vars} vs 실제 {actual_vars} "
                    f"(차이 {ratio:.0%})"
                )

    # ── 4. 컴파일 경고 분석 ──
    unknown_op_count = 0
    type_error_count = 0
    for w in compile_warnings:
        w_str = str(w).lower()
        if "unknown operator" in w_str:
            unknown_op_count += 1
        if "type" in w_str and ("error" in w_str or "incompatible" in w_str):
            type_error_count += 1

    if unknown_op_count > 0:
        warnings.append(f"미지원 연산자 경고 {unknown_op_count}건 — 일부 제약이 무시됨")

    if type_error_count > 0:
        warnings.append(f"타입 오류 {type_error_count}건 — 파라미터 변환 문제 가능성")

    stats["unknown_operator_warnings"] = unknown_op_count
    stats["type_error_warnings"] = type_error_count

    # ── 5. 결과 판정 ──
    should_pass = len(errors) == 0

    result = {
        "pass": should_pass,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }

    logger.info(
        f"Gate3: pass={should_pass}, errors={len(errors)}, "
        f"warnings={len(warnings)}, vars={actual_vars}, "
        f"constraints={actual_constraints}"
    )

    return result


def to_text_summary(result: Dict) -> str:
    """Gate 3 결과를 텍스트로 변환"""
    stats = result.get("stats", {})
    lines = [
        f"[컴파일 검증] pass={result['pass']}",
        f"변수: {stats.get('actual_variables', '?')}개, "
        f"제약: {stats.get('actual_constraints', '?')}개, "
        f"컴파일 시간: {stats.get('compile_time', '?')}s",
    ]

    if "hard_apply_ratio" in stats:
        lines.append(
            f"hard 제약 적용률: {stats['hard_apply_ratio']:.0%} "
            f"(실패 {stats.get('failed_constraints', 0)}건, "
            f"soft 스킵 {stats.get('skipped_soft', 0)}건)"
        )

    if result["errors"]:
        lines.append(f"\n❌ 오류 ({len(result['errors'])}개):")
        for e in result["errors"]:
            lines.append(f"  - {e}")

    if result["warnings"]:
        lines.append(f"\n⚠ 경고 ({len(result['warnings'])}개):")
        for w in result["warnings"]:
            lines.append(f"  - {w}")

    return "\n".join(lines)
