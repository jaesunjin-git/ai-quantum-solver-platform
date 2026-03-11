"""
core/platform/utils.py
───────────────────────
플랫폼 공통 유틸리티 함수 모음 (도메인 무관).

세션 상태(SessionState)를 인자로 받아 처리하며,
LLM 모델이나 DB 의존성이 없는 순수 함수들입니다.

원래 domains/crew/utils.py에서 core/platform/으로 이동.
기존 import 경로는 re-export wrapper로 호환 유지.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# NOTE: Step 2에서 session.py 분리 후 import 경로가 변경됩니다
# from domains.crew.session import SessionState
# 현재는 agent.py에 SessionState가 있으므로 파라미터 타입 힌트만 사용

def build_facts_summary(facts: dict) -> str:
    """팩트 데이터를 프롬프트용 텍스트로 변환"""
    if not facts:
        return ""

    lines = ["[VERIFIED DATA FACTS - 코드로 계산된 확정값, 절대 변경 금지]"]
    lines.append(f"총 파일 수: {len(facts.get('files', []))}개")
    lines.append(f"총 레코드 수: {facts.get('total_records', 0):,}개")

    for f in facts.get("files", []):
        lines.append(f"\n파일: {f['name']} ({f['type']})")
        lines.append(f"  레코드 수: {f.get('records', 0):,}개")
        if f.get('columns') and isinstance(f['columns'], list):
            lines.append(f"  컬럼 수: {len(f['columns'])}개")

    # 시트 정보
    for filename, sheets in facts.get("sheet_info", {}).items():
        for sheet_name, info in sheets.items():
            lines.append(f"  [{filename} → {sheet_name}] 행: {info['rows']:,}, 열: {info['cols']}")

    # 주요 고유값 수 (집합 크기 추정에 활용)
    unique = facts.get("unique_counts", {})
    if unique:
        lines.append("\n[주요 컬럼별 고유값 수 - 집합(Set) 크기 산정 근거]")
        for key, count in sorted(unique.items(), key=lambda x: -x[1])[:20]:
            lines.append(f"  {key}: {count:,}개")

    return "\n".join(lines)


def clean_report(raw: str) -> str:
    """내부 지시문 제거"""
    cleaned = re.sub(r'^⛔.*$', '', raw, flags=re.MULTILINE)
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'\[내부\s*검증[^\]]*\].*?(?=\n##|\n---|\Z)', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'SYSTEM[- ]?LOCKED.*?(?=\n##|\n---|\Z)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'^.*절대\s*변경하지\s*마.*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^.*출력을\s*종료하세요.*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def extract_text_from_llm(text: str) -> str:
    """LLM 응답에서 JSON을 제거하고 자연어 텍스트만 추출"""
    # JSON 블록 제거
    cleaned = re.sub(r'```json\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'\{[^{}]*"tool_code"[^{}]*\}', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'\{[^{}]*"tool_name"[^{}]*\}', '', cleaned, flags=re.DOTALL)
    # ★ 추가: 스킬명 패턴 제거
    skill_names = [
        "AnalyzeDataSkill", "MathModelSkill", "PreDecisionSkill",
        "StartOptimizationSkill", "ShowResultSkill", "AnswerQuestionSkill",
        "GeneralReplySkill", "FileReceivedSkill", "UpdateWorkspaceSkill",
        "AskForDataSkill",
    ]
    for skill in skill_names:
        cleaned = cleaned.replace(skill, "")
    # "~실행", "~수행" 패턴 제거
    cleaned = re.sub(r'`[^`]*Skill`\s*실행\.?', '', cleaned)
    cleaned = re.sub(r'[A-Za-z]+Skill\s*실행\.?', '', cleaned)
    cleaned = re.sub(r'[A-Za-z]+Skill\s*수행\.?', '', cleaned)

    cleaned = clean_report(cleaned)
    cleaned = clean_report(cleaned)
    if not cleaned.strip():
        return "무엇을 도와드릴까요? 아래 버튼을 눌러 다음 단계를 진행해 보세요."
    return cleaned


def domain_display(domain: Optional[str]) -> str:
    """도메인 프로파일 YAML에서 icon + display_name 로드. 미등록 도메인은 fallback."""
    return _get_domain_display_map().get(domain, f"🔧 {domain or 'Unknown'}")


def _get_domain_display_map() -> Dict[str, str]:
    """domain_profiles.yaml에서 display_map 캐시 로드."""
    if not hasattr(_get_domain_display_map, "_cache"):
        display_map: Dict[str, str] = {}
        try:
            from pathlib import Path
            import yaml
            profiles_path = Path(__file__).resolve().parent.parent.parent / "knowledge" / "domain_profiles.yaml"
            if profiles_path.exists():
                with open(profiles_path, encoding="utf-8") as f:
                    profiles = yaml.safe_load(f) or {}
                for key, val in profiles.items():
                    if isinstance(val, dict):
                        icon = val.get("icon", "🔧")
                        name = val.get("display_name", key)
                        display_map[key] = f"{icon} {name}"
        except Exception:
            pass
        # "general" alias for "generic"
        if "generic" in display_map and "general" not in display_map:
            display_map["general"] = display_map["generic"]
        _get_domain_display_map._cache = display_map
    return _get_domain_display_map._cache


def build_guide_text(state: SessionState) -> str:
    lines = ["📖 **워크플로 가이드**\n"]
    steps = [
        ("1️⃣", "파일 업로드", state.file_uploaded),
        ("2️⃣", "데이터 분석", state.analysis_completed),
        ("3️⃣", "문제 정의", getattr(state, 'problem_defined', False)),
        ("4️⃣", "데이터 정규화", getattr(state, 'data_normalized', False)),
        ("5️⃣", "수학 모델 생성", state.math_model_confirmed),
        ("6️⃣", "솔버 실행", state.optimization_done),
    ]
    for icon, label, done in steps:
        status = "✅" if done else "⬜"
        lines.append(f"{icon} {status} {label}")
    lines.append(f"\n현재 상태: {state.context_string()}")
    return "\n".join(lines)


def build_next_options(state: SessionState) -> List[Dict]:
    if not state.file_uploaded:
        return [
            {"label": "📁 파일 업로드", "action": "upload"},
            {"label": "📖 가이드", "action": "send", "message": "가이드"},
        ]
    if not state.analysis_completed:
        return [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}]
    if not getattr(state, 'problem_defined', False):
        return [
            {"label": "📋 문제 정의 시작", "action": "send", "message": "문제 정의 시작"},
            {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
        ]
    if not getattr(state, 'data_normalized', False):
        return [
            {"label": "📊 데이터 정규화", "action": "send", "message": "데이터 정규화 시작"},
            {"label": "📋 문제 정의 수정", "action": "send", "message": "문제 정의 수정"},
        ]
    if not state.math_model_confirmed:
        return [
            {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
            {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
        ]
    if not state.pre_decision_done:
        return [
            {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
            {"label": "📐 수학 모델", "action": "send", "message": "수학 모델 보여줘"},
        ]
    if not state.optimization_done:
        return [
            {"label": "🚀 최적화 실행", "action": "send", "message": "최적화 실행해줘"},
            {"label": "⚡ 솔버 결과", "action": "send", "message": "솔버 결과 보여줘"},
        ]
    return [
        {"label": "📈 최적화 결과", "action": "send", "message": "최적화 결과 보여줘"},
        {"label": "📥 다운로드", "action": "download"},
        {"label": "🔙 처음부터", "action": "send", "message": "리셋"},
    ]


# error_response는 core.platform.errors로 이동 — 하위 호환용 re-export
from core.platform.errors import error_response, warning_response, ErrorCode  # noqa: F401
