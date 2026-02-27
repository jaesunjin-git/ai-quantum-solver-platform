"""
domains/crew/utils.py
─────────────────────
CrewAgent에서 분리된 순수 헬퍼 함수 모음.
세션 상태(SessionState)를 인자로 받아 처리하며,
LLM 모델이나 DB 의존성이 없는 순수 함수들입니다.

리팩토링 Step 1에서 agent.py(~1868줄)로부터 추출됨.
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
    display_map = {
        "aviation": "✈️ 항공 (Aviation)",
        "railway": "🚄 철도 (Railway)",
        "bus": "🚌 버스 (Bus)",
        "logistics": "📦 물류 (Logistics)",
        "hospital": "🏥 병원 (Hospital)",
        "general": "🔧 일반 (General)",
    }
    return display_map.get(domain, f"🔧 {domain or '미감지'}")


def build_guide_text(state: SessionState) -> str:
    lines = ["📖 **워크플로 가이드**\n"]
    steps = [
        ("1️⃣", "파일 업로드", state.file_uploaded),
        ("2️⃣", "데이터 분석", state.analysis_completed),
        ("3️⃣", "수학 모델 생성", state.math_model_confirmed),
        ("4️⃣", "솔버 추천", state.pre_decision_done),
        ("5️⃣", "최적화 실행", state.optimization_done),
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


def error_response(text: str, retry_msg: str = "다시 시도") -> Dict:
    return {
        "type": "error",
        "text": f"❌ {text}",
        "data": None,
        "options": [
            {"label": "🔄 다시 시도", "action": "send", "message": retry_msg},
            {"label": "📖 가이드", "action": "send", "message": "가이드"},
        ],
    }
