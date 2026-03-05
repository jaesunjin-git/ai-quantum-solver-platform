"""
domains/crew/classifier.py
──────────────────────────
사용자 입력 의도 분류 모듈.

InputClassifier: 키워드 기반 빠른 의도 분류 (LLM 호출 없이 라우팅)
  - quick_classify(): 메시지 → intent 매핑
  - extract_domain_from_message(): 텍스트에서 도메인 키워드 감지

SKILL_TO_INTENT: LLM이 반환한 Skill명 → 내부 intent 코드 매핑

parse_skill_from_llm(): LLM 응답 텍스트에서 Skill JSON 추출 및 파싱

리팩토링 Step 3에서 agent.py로부터 추출됨.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class InputClassifier:
    _YAML_PATH = Path(__file__).parents[2] / "configs" / "classifier_keywords.yaml"
    _keywords: Dict[str, List[str]] = {}
    _domain_map: Dict[str, str] = {}
    _loaded: bool = False

    @classmethod
    def _load_keywords(cls):
        if cls._loaded:
            return
        try:
            with open(cls._YAML_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            cls._keywords = {k: v for k, v in raw.items() if k != "domain_keyword_map"}
            cls._domain_map = raw.get("domain_keyword_map", {})
            cls._loaded = True
            logger.info("classifier keywords loaded from YAML")
        except Exception as e:
            logger.warning(f"YAML load failed ({e}), using defaults")
            cls._load_defaults()
            cls._loaded = True

    @classmethod
    def _load_defaults(cls):
        cls._keywords = {
            "analysis": ["분석", "analyze", "데이터 분석", "리포트"],
            "analysis_result": ["분석 결과", "분석결과", "리포트 보여"],
            "structural_normalization": ["구조 정규화", "구조정규화", "structural normalization", "Phase 1", "phase1", "구조 변환"],
            "data_normalization": ["데이터 정규화", "정규화", "normalization", "데이터 변환", "매핑"],
            "problem_definition": ["문제 정의", "문제정의", "problem definition", "문제 유형", "어떤 문제", "what to optimize", "최적화 문제 정의"],
            "math_model": ["수학 모델", "수학모델", "모델링", "modeling", "수식", "변수 정의", "제약 정의"],
            "pre_decision": ["솔버", "solver", "추천", "recommend", "시뮬레이션", "정확도 우선", "속도 우선", "비용 우선"],
            "execution": ["실행", "execute", "run", "최적화 실행"],
            "show_math_model": ["수학 모델 보여", "모델 보여", "모델 확인", "현재 모델"],
            "show_result": ["결과", "result", "결과 보여"],
            "show_solver": ["솔버 결과", "추천 결과"],
            "show_opt_result": ["최적화 결과", "최종 결과"],
            "reset": ["리셋", "reset", "초기화", "처음부터"],
            "guide": ["도움", "help", "가이드", "뭐해", "다음 단계"],
            "domain_change": ["도메인 변경", "도메인 수정"],
        }
        cls._domain_map = {
            "항공": "aviation", "철도": "railway", "버스": "bus",
            "물류": "logistics", "병원": "hospital",
        }

    @classmethod
    def quick_classify(cls, message: str, has_file: bool = False, current_tab: Optional[str] = None) -> Optional[str]:
        """
        키워드 매칭으로 빠르게 분류. 확실한 경우만 반환.
        애매하면 None → LLM에게 위임.
        """
        cls._load_keywords()

        if has_file and not message.strip():
            return "FILE_UPLOAD"

        msg = message.lower().strip()

        # ★ 질문 패턴 감지: 질문어미가 있으면 LLM에게 넘김
        question_endings = [
            "인가요?", "인가요", "뭔가요?", "뭔가요", "건가요?", "건가요",
            "나요?", "나요", "할까요?", "할까요", "을까요?", "을까요",
            "는지요?", "는지요", "는건지", "어떤가요?", "어떤가요",
            "어떻게", "왜", "무엇", "뭐가", "뭘",
            "알려주세요", "알려줘", "설명해주세요", "설명해줘",
            "파악되나요", "되나요", "있나요", "없나요",
        ]
        if any(msg.endswith(q) or q in msg for q in question_endings):
            # 단, 명시적 실행 요청("~해줘", "~시작")이 함께 있으면 키워드 매칭 진행
            action_keywords = ["해줘", "시작", "실행", "생성해", "확정", "추천해"]
            if not any(ak in msg for ak in action_keywords):
                return None

        # 특수 명령 (항상 키워드로 처리)
        for intent in ["reset", "guide", "domain_change"]:
            if any(kw in msg for kw in cls._keywords.get(intent, [])):
                return intent.upper()

        # 파일 + 명령 동시 → 명령 우선
        if has_file:
            for intent in ["analysis", "execution", "pre_decision"]:
                if any(kw in msg for kw in cls._keywords.get(intent, [])):
                    return intent.upper() if intent != "analysis" else "ANALYZE"
            return "FILE_UPLOAD"

        # 명확한 키워드 매칭
        if any(kw in msg for kw in cls._keywords.get("execution", [])):
            return "START_OPTIMIZATION"
        if any(kw in msg for kw in cls._keywords.get("show_opt_result", [])):
            return "SHOW_OPT_RESULT"
        if any(kw in msg for kw in cls._keywords.get("show_solver", [])):
            return "SHOW_SOLVER"
        if any(kw in msg for kw in cls._keywords.get("show_math_model", [])):
            return "SHOW_MATH_MODEL"
        if any(kw in msg for kw in cls._keywords.get("data_normalization", [])):
            return "DATA_NORMALIZATION"
        if any(kw in msg for kw in cls._keywords.get("problem_definition", [])):
            return "PROBLEM_DEFINITION"
        if any(kw in msg for kw in cls._keywords.get("math_model", [])):
            return "MATH_MODEL"
        if any(kw in msg for kw in cls._keywords.get("show_result", [])):
            return "SHOW_RESULT"
        if any(kw in msg for kw in cls._keywords.get("pre_decision", [])):
            return "PRE_DECISION"
        if any(kw in msg for kw in cls._keywords.get("analysis_result", [])):
            return "SHOW_ANALYSIS"
        if any(kw in msg for kw in cls._keywords.get("analysis", [])):
            return "ANALYZE"

        # ── Tab-context aware classification ──
        # 1순위: 메시지에 명시적 대상 + 실행 동사
        tab_keyword_map = {
            "analysis": ["분석", "데이터 분석", "리포트", "analyze"],
            "structural_normalization": ["구조 정규화", "구조정규화", "structural normalization", "Phase 1", "phase1", "구조 변환"],
            "data_normalization": ["데이터 정규화", "정규화", "normalization", "데이터 변환", "매핑"],
            "problem_definition": ["문제 정의", "문제정의", "problem definition"],
            "math_model": ["수학 모델", "수학모델", "모델링", "목적함수", "제약조건", "변수", "수식"],
            "solver": ["솔버", "solver", "추천", "컴파일"],
            "result": ["결과", "실행", "최적화"],
        }
        intent_from_tab = {
            "analysis": "ANALYZE",
            "problem_definition": "PROBLEM_DEFINITION",
            "math_model": "MATH_MODEL",
            "solver": "PRE_DECISION",
            "result": "START_OPTIMIZATION",
        }
        action_verbs = ["해줘", "해주세요", "시작", "실행", "생성", "바꿔", "변경", "수정", "다시", "재생성"]

        for tab_key, keywords in tab_keyword_map.items():
            if any(kw in msg for kw in keywords):
                if any(v in msg for v in action_verbs):
                    resolved = intent_from_tab.get(tab_key)
                    if resolved:
                        logger.info(f"Keyword+verb resolved: {tab_key} -> {resolved}")
                        return resolved
                break  # Found target but no action verb -> let LLM handle

        # 2순위: 모호한 메시지 + current_tab 컨텍스트
        if current_tab:
            if any(v in msg for v in action_verbs):
                resolved = intent_from_tab.get(current_tab)
                if resolved:
                    logger.info(f"Tab-context resolved: tab={current_tab} -> {resolved}")
                    return resolved

        # 매칭 안 됨 → LLM에게 위임
        return None

    @classmethod
    def extract_domain_from_message(cls, message: str) -> Optional[str]:
        cls._load_keywords()
        msg = message.lower()
        for keyword, domain in cls._domain_map.items():
            if keyword in msg:
                return domain
        return None


# ============================================================
# 4. LLM 응답 파서 (Skill JSON 추출)
# ============================================================

# Skill명 → 내부 intent 매핑
SKILL_TO_INTENT = {
    "FileReceivedSkill": "FILE_UPLOAD",
    "AnalyzeDataSkill": "ANALYZE",
    "ProblemDefinitionSkill": "PROBLEM_DEFINITION",
    "StructuralNormalizationSkill": "STRUCTURAL_NORMALIZATION",
    "DataNormalizationSkill": "DATA_NORMALIZATION",
    "PreDecisionSkill": "PRE_DECISION",
    "MathModelSkill": "MATH_MODEL",
    "StartOptimizationSkill": "START_OPTIMIZATION",
    "ShowResultSkill": "SHOW_OPT_RESULT",
    "AnswerQuestionSkill": "ANSWER",
    "GeneralReplySkill": "GENERAL",
    "UpdateWorkspaceSkill": "UPDATE_WORKSPACE",
    "AskForDataSkill": "ASK_FOR_DATA",
}


def parse_skill_from_llm(response_text: str) -> tuple[Optional[str], Dict[str, Any]]:
    """
    LLM 응답에서 Skill JSON을 추출.
    반환: (intent, parameters) 또는 (None, {}) if 파싱 실패
    """
    text = response_text.strip()

    # 마크다운 코드블록 제거
    code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_match:
        text = code_match.group(1)

    # JSON 추출 시도
    json_str = None

    # 1) 중괄호로 시작하는 JSON 찾기
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        json_str = brace_match.group(0)

    if not json_str:
        return None, {}

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        return None, {}

    # skill / tool_code / tool_name 중 하나에서 스킬명 추출
    skill_name = (
        parsed.get("skill")
        or parsed.get("tool_code")
        or parsed.get("tool_name")
        or ""
    )
    parameters = parsed.get("parameters", {})

    intent = SKILL_TO_INTENT.get(skill_name)
    if intent:
        return intent, parameters

    # 부분 매칭
    for known_skill, mapped_intent in SKILL_TO_INTENT.items():
        if known_skill.lower() in skill_name.lower():
            return mapped_intent, parameters

    return None, {}
