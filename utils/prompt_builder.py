# utils/prompt_builder.py — v3.0
# ============================================================
# v2.0 : detected_domain, domain_confidence 파라미터 추가
# v2.1 : domain_instruction을 HTML 주석으로 변경
# v3.0 : 하드코딩 DOMAIN_PROFILES 제거 → knowledge/domain_profiles.yaml 로드
#         YAML 구조(terminology, typical_constraints, typical_objectives,
#         regulations, detection_keywords)를 프롬프트에 반영
# ============================================================

import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ============================================================
# YAML 경로
# ============================================================
_PROFILES_PATH = Path(__file__).parents[1] / "knowledge" / "domain_profiles.yaml"
_TEMPLATE_PATH = Path(__file__).parents[1] / "prompts" / "analysis_report.md"

# 캐시 (한 번만 로드)
_profiles_cache: Optional[Dict[str, Any]] = None


# ============================================================
# 프로파일 로더
# ============================================================
def _load_profiles() -> Dict[str, Any]:
    """knowledge/domain_profiles.yaml을 로드하여 캐시. 실패 시 최소 fallback."""
    global _profiles_cache
    if _profiles_cache is not None:
        return _profiles_cache

    try:
        with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("YAML root must be a dict")
        _profiles_cache = data
        logger.info(f"domain_profiles.yaml loaded: {list(data.keys())}")
        return _profiles_cache
    except Exception as e:
        logger.warning(f"domain_profiles.yaml load failed ({e}), using minimal fallback")
        _profiles_cache = {
            "generic": {
                "display_name": "일반 인력 스케줄링",
                "icon": "👥",
                "description": "범용 인력/자원 스케줄링 최적화",
                "terminology": {"crew": "인력", "shift": "교대", "route": "작업"},
                "typical_constraints": {"hard": [], "soft": []},
                "typical_objectives": [],
                "regulations": [],
            }
        }
        return _profiles_cache


def invalidate_cache():
    """개발/테스트용 캐시 무효화. domain_profiles.yaml 수정 후 호출."""
    global _profiles_cache
    _profiles_cache = None


def get_profile(domain: str) -> Dict[str, Any]:
    """도메인 키로 프로파일 조회. 없으면 generic 반환."""
    profiles = _load_profiles()
    # 정확 매칭 → generic fallback
    if domain in profiles:
        return profiles[domain]
    # 'general' 요청 시 generic으로 매핑
    if domain == "general" and "generic" in profiles:
        return profiles["generic"]
    return profiles.get("generic", {})


# ============================================================
# 템플릿 로더
# ============================================================
def _load_template() -> str:
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("analysis_report.md not found, using fallback template")
        return _get_fallback_template()


def _get_fallback_template() -> str:
    return """## 1. 도메인 감지 결과
| 항목 | 값 |
|:-----|:-----|
| 감지된 도메인 | {detected_domain_display} |
| 확신도 | {confidence_status} |

{domain_instruction}

## 2. 데이터 개요
업로드된 데이터를 기반으로 분석합니다.

## 3. 최적화 목표 추론

## 4. 주요 변수 식별

## 5. 제약 조건 분석

## 6. 데이터 품질 이슈
"""


# ============================================================
# 프로파일 → 프롬프트 텍스트 변환 헬퍼
# ============================================================
def _build_terminology_table(profile: Dict[str, Any]) -> str:
    """terminology 딕셔너리를 마크다운 테이블로 변환"""
    terminology = profile.get("terminology", {})
    if not terminology:
        return "(용어 정보 없음)"

    lines = [
        "| 구분 | 도메인 용어 |",
        "|:-----|:-----|",
    ]
    for key, value in terminology.items():
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


def _build_constraints_text(profile: Dict[str, Any]) -> str:
    """typical_constraints를 읽기 좋은 텍스트로 변환"""
    constraints = profile.get("typical_constraints", {})
    if not constraints:
        return "일반적 제약 조건"

    parts = []

    hard = constraints.get("hard", [])
    if hard:
        parts.append("**Hard 제약**: " + ", ".join(hard))

    soft = constraints.get("soft", [])
    if soft:
        parts.append("**Soft 제약**: " + ", ".join(soft))

    return "\n".join(parts) if parts else "일반적 제약 조건"


def _build_objectives_text(profile: Dict[str, Any]) -> str:
    """typical_objectives를 텍스트로 변환"""
    objectives = profile.get("typical_objectives", [])
    if not objectives:
        return ""
    return ", ".join(objectives)


def _build_regulations_text(profile: Dict[str, Any]) -> str:
    """regulations를 텍스트로 변환"""
    regulations = profile.get("regulations", [])
    if not regulations:
        return ""
    return ", ".join(regulations)


def _build_domain_context(profile: Dict[str, Any]) -> str:
    """프로파일 전체를 LLM이 참고할 도메인 컨텍스트 블록으로 조립"""
    sections = []

    # 설명
    desc = profile.get("description", "")
    if desc:
        sections.append(f"도메인 설명: {desc}")

    # 용어
    term_table = _build_terminology_table(profile)
    sections.append(f"도메인 용어:\n{term_table}")

    # 제약 조건
    constraints_text = _build_constraints_text(profile)
    sections.append(f"일반적 제약 조건:\n{constraints_text}")

    # 목적 함수
    obj_text = _build_objectives_text(profile)
    if obj_text:
        sections.append(f"일반적 최적화 목표: {obj_text}")

    # 규정
    reg_text = _build_regulations_text(profile)
    if reg_text:
        sections.append(f"관련 규정: {reg_text}")

    return "\n\n".join(sections)


# ============================================================
# 메인 빌더
# ============================================================
def build_analysis_prompt(
    csv_summary: str,
    context: str = "",
    detected_domain: Optional[str] = None,
    domain_confidence: float = 0.0,
    domain_override: Optional[str] = None,
    data_facts: str = "",  # ★ 추가
    data_profile_text: str = "",  # ★ Gate 1 프로파일
) -> str:
    """분석 프롬프트를 조립하여 반환"""

    # ── 1) 도메인 결정: override > detected > generic ──
    domain = domain_override or detected_domain or "generic"
    confidence = 1.0 if domain_override else domain_confidence

    # ── 2) 프로파일 추출 ──
    profile = get_profile(domain)
    display_name = profile.get("display_name", domain)
    icon = profile.get("icon", "🔧")

    # ── 3) 확신도 표시 ──
    confidence_pct = int(confidence * 100)
    if confidence_pct >= 80:
        status_label = f"✅ {confidence_pct}% (높음)"
    elif confidence_pct >= 50:
        status_label = f"⚠️ {confidence_pct}% (보통)"
    else:
        status_label = f"⚠️ {confidence_pct}% (낮음)"

    # ── 4) 도메인 지시문 (HTML 주석 — LLM이 참고하되 출력 안 함) ──
    domain_instruction = (
        f"<!-- SYSTEM-LOCKED\n"
        f"도메인: {icon} {display_name}\n"
        f"확신도: {confidence_pct}%\n"
        f"위 값은 시스템 확정값입니다. 절대 변경하지 마십시오.\n"
        f"이 주석 블록을 출력에 포함하지 마십시오.\n"
        f"-->"
    )

    # ── 5) 도메인 컨텍스트 블록 ──
    domain_context = _build_domain_context(profile)

    # ── 6) 플레이스홀더용 개별 값 ──
    term_table = _build_terminology_table(profile)
    constraints_text = _build_constraints_text(profile)
    objectives_text = _build_objectives_text(profile)
    regulations_text = _build_regulations_text(profile)

    # ── 7) 템플릿 로드 & 플레이스홀더 치환 ──
    template = _load_template()

    # terminology 빈 값 기본값 처리
    if not term_table or not term_table.strip():
        term_table = "(도메인 특화 용어 없음 — 일반 용어 사용)"

    replacements = {
        "{detected_domain_display}": f"{icon} {display_name}",
        "{confidence_status}": status_label,
        "{domain_instruction}": domain_instruction,
        "{terminology_table}": term_table,
        "{domain_terminology_table}": term_table,
        "{common_constraints}": constraints_text,
        "{typical_objectives}": objectives_text,
        "{regulations}": regulations_text,
        "{domain_description}": profile.get("description", ""),
        # analysis_report.md v3.2 템플릿 변수
        "{domain_icon}": icon,
        "{domain_display_name}": display_name,
        "{domain_confidence}": f"{confidence_pct}%",
    }

    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)

    # ── 8) 최종 조립 ──
    # domain_instruction은 템플릿 내 {domain_instruction} 치환으로 이미 포함됨 — 중복 제거
    sections = [
        f"## DOMAIN CONTEXT\n{domain_context}",
        prompt,
    ]

    if csv_summary:
        sections.append(f"\n---\n## DATA SUMMARY\n```\n{csv_summary}\n```")

    if context:
        # HTML 주석 injection 방지: --> 문자열 escape
        safe_context = context.replace("-->", "——>")
        sections.insert(0,
            f"<!-- SYSTEM-INTERNAL: DO NOT include the following state information in your output. "
            f"This is for your reference only to understand the current pipeline status. -->\n"
            f"<!-- STATE: {safe_context} -->\n"
            f"<!-- END SYSTEM-INTERNAL -->"
        )
    # ★ 추가: 팩트 데이터 (코드로 계산된 확정값)
    if data_facts:
        sections.append(
            f"\n---\n## VERIFIED DATA FACTS\n"
            f"아래는 코드로 계산된 정확한 수치입니다. "
            f"리포트 작성 시 이 수치를 반드시 그대로 사용하세요. "
            f"절대 다른 숫자로 변경하거나 추정하지 마세요.\n\n"
            f"{data_facts}"
        )

    # ★ Gate 1: 데이터 프로파일 (규칙 기반 자동 감지 결과)
    if data_profile_text:
        sections.append(
            f"\n---\n## DATA PROFILE (자동 감지)\n"
            f"아래는 코드가 자동으로 감지한 데이터 구조 정보입니다. "
            f"분석 시 이 정보를 참고하여 데이터 타입, 결측치, 비정형 구조를 정확히 반영하세요.\n\n"
            f"{data_profile_text}"
        )

    final_prompt = "\n\n".join(sections)

    logger.info(
        f"Prompt built: domain={domain}, confidence={confidence_pct}%, "
        f"profile_keys={list(profile.keys())}, final_len={len(final_prompt)}"
    )

    return final_prompt