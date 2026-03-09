"""
domains/crew/classifier.py
──────────────────────────
사용자 입력 의도 분류 모듈.

InputClassifier: 키워드 기반 빠른 의도 분류 (LLM 호출 없이 라우팅)
  - quick_classify(): 메시지 → intent 매핑
  - extract_domain_from_message(): 텍스트에서 도메인 키워드 감지

모든 패턴/키워드는 configs/classifier_keywords.yaml에서 로드됩니다.
하드코딩 없이 YAML 수정만으로 동작 변경 가능.

SKILL_TO_INTENT: LLM이 반환한 Skill명 → 내부 intent 코드 매핑
parse_skill_from_llm(): LLM 응답 텍스트에서 Skill JSON 추출 및 파싱
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
    # 질문 패턴 (YAML question_patterns 섹션)
    _question_endings: List[str] = []
    _question_catch_all_suffix: str = "?"
    _question_action_overrides: List[str] = []
    # question guard (YAML question_guard 섹션)
    _guard_markers: List[str] = []
    _guard_action_overrides: List[str] = []
    # tab keyword map (YAML tab_keyword_map 섹션)
    _tab_keyword_map: Dict[str, Dict] = {}
    # action verbs (YAML action_verbs 섹션)
    _action_verbs: List[str] = []
    _loaded: bool = False

    @classmethod
    def reload(cls):
        """캐시를 초기화하고 YAML을 다시 로드합니다."""
        cls._loaded = False
        cls._load_keywords()

    @classmethod
    def _load_keywords(cls):
        if cls._loaded:
            return
        try:
            with open(cls._YAML_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)

            # 기존 intent 키워드
            _reserved = {"domain_keyword_map", "question_patterns", "question_guard",
                         "tab_keyword_map", "action_verbs"}
            cls._keywords = {k: v for k, v in raw.items()
                             if k not in _reserved and isinstance(v, list)}
            cls._domain_map = raw.get("domain_keyword_map", {})

            # 질문 패턴
            qp = raw.get("question_patterns", {})
            cls._question_endings = qp.get("endings", [])
            cls._question_catch_all_suffix = qp.get("catch_all_suffix", "?")
            cls._question_action_overrides = qp.get("action_overrides", [])

            # question guard
            qg = raw.get("question_guard", {})
            cls._guard_markers = qg.get("markers", [])
            cls._guard_action_overrides = qg.get("action_overrides", [])

            # tab keyword map
            cls._tab_keyword_map = raw.get("tab_keyword_map", {})

            # action verbs
            cls._action_verbs = raw.get("action_verbs", [])

            cls._loaded = True
            logger.info("classifier keywords loaded from YAML")
        except Exception as e:
            logger.warning(f"YAML load failed ({e}), using defaults")
            cls._load_defaults()
            cls._loaded = True

    @classmethod
    def _load_defaults(cls):
        """YAML 로드 실패 시 최소 fallback"""
        cls._keywords = {
            "analysis": ["분석해줘", "분석 시작", "analyze"],
            "execution": ["최적화 실행", "실행해줘", "실행 시작"],
            "show_math_model": ["수학 모델 보여줘", "모델 보여줘"],
            "show_opt_result": ["최적화 결과", "최종 결과"],
            "show_result": ["결과 보여줘", "결과 확인"],
            "show_solver": ["솔버 결과", "추천 결과"],
            "reset": ["리셋", "reset", "초기화", "처음부터"],
            "guide": ["다음 단계", "뭘 해야", "guide", "help"],
            "domain_change": ["도메인 변경", "도메인 수정"],
        }
        cls._domain_map = {
            "항공": "aviation", "철도": "railway", "버스": "bus",
            "물류": "logistics", "병원": "hospital",
        }
        cls._question_endings = ["인가요", "나요", "할까요", "어떻게", "왜", "알려줘", "설명해줘"]
        cls._question_catch_all_suffix = "?"
        cls._question_action_overrides = ["해줘", "시작", "실행", "생성해", "확정"]
        cls._guard_markers = ["인가요", "나요", "할까요", "알려줘", "설명해", "?", "궁금"]
        cls._guard_action_overrides = ["해줘", "시작", "실행", "생성해", "확정", "추천해", "다시"]
        cls._tab_keyword_map = {}
        cls._action_verbs = ["해줘", "해주세요", "시작", "실행", "생성", "바꿔", "수정", "다시"]

    # ----------------------------------------------------------
    # Public: question guard 데이터 제공 (agent.py에서 사용)
    # ----------------------------------------------------------
    @classmethod
    def get_question_guard_config(cls) -> tuple[List[str], List[str]]:
        """(markers, action_overrides) 반환 — agent.py _run_inner에서 사용"""
        cls._load_keywords()
        return cls._guard_markers, cls._guard_action_overrides

    # ----------------------------------------------------------
    # 빠른 의도 분류
    # ----------------------------------------------------------
    @classmethod
    def quick_classify(cls, message: str, has_file: bool = False,
                       current_tab: Optional[str] = None) -> Optional[str]:
        """
        키워드 매칭으로 빠르게 분류. 확실한 경우만 반환.
        애매하거나 질문이면 None → LLM에게 위임.

        결정 순서:
          1. 파일만 있고 메시지 없음 → FILE_UPLOAD
          2. 질문 패턴 감지 → None (LLM)
          3. 특수 명령 (RESET / GUIDE / DOMAIN_CHANGE)
          4. 파일 + 명령 동시
          5. 명시적 action 키워드 (execution / show_*)
          6. keyword + action_verb 조합 (tab_keyword_map)
          7. current_tab + action_verb 조합
          8. None (LLM)
        """
        cls._load_keywords()

        # ── 1. 파일만 있고 메시지 없음 ──
        if has_file and not message.strip():
            return "FILE_UPLOAD"

        msg = message.lower().strip()

        # ── 2. 질문 패턴 감지 ──
        is_question = (
            any(msg.endswith(q) or q in msg for q in cls._question_endings)
            or msg.endswith(cls._question_catch_all_suffix)
        )
        if is_question:
            if not any(ao in msg for ao in cls._question_action_overrides):
                return None  # 질문 → LLM

        # ── 3. 특수 명령 (항상 키워드로 처리) ──
        for intent in ["reset", "guide", "domain_change"]:
            if any(kw in msg for kw in cls._keywords.get(intent, [])):
                return intent.upper()

        # ── 4. 파일 + 명령 동시 → 명령 우선 ──
        if has_file:
            for intent in ["analysis", "execution", "pre_decision"]:
                if any(kw in msg for kw in cls._keywords.get(intent, [])):
                    return "ANALYZE" if intent == "analysis" else intent.upper()
            return "FILE_UPLOAD"

        # ── 5. 명시적 action 키워드 (동사 포함된 복합 표현만) ──
        if any(kw in msg for kw in cls._keywords.get("execution", [])):
            return "START_OPTIMIZATION"
        if any(kw in msg for kw in cls._keywords.get("show_opt_result", [])):
            return "SHOW_OPT_RESULT"
        if any(kw in msg for kw in cls._keywords.get("show_solver", [])):
            return "SHOW_SOLVER"
        if any(kw in msg for kw in cls._keywords.get("show_math_model", [])):
            return "SHOW_MATH_MODEL"
        if any(kw in msg for kw in cls._keywords.get("show_result", [])):
            return "SHOW_RESULT"
        if any(kw in msg for kw in cls._keywords.get("analysis_result", [])):
            return "SHOW_ANALYSIS"
        # analysis / math_model / problem_definition / data_normalization / pre_decision:
        # 반드시 action_verb와 함께 있어야 함 (아래 tab_keyword_map 섹션에서 처리)
        # 단독 명사 매칭은 LLM에게 위임 → 질문일 수 있음

        # ── 6. keyword + action_verb 조합 (YAML tab_keyword_map) ──
        for tab_key, tab_cfg in cls._tab_keyword_map.items():
            keywords = tab_cfg.get("keywords", [])
            intent = tab_cfg.get("intent")
            if not intent:
                continue
            if any(kw in msg for kw in keywords):
                if any(v in msg for v in cls._action_verbs):
                    logger.info(f"Keyword+verb resolved: {tab_key} -> {intent}")
                    return intent
                break  # 키워드는 있지만 동사 없음 → LLM

        # ── 7. current_tab + action_verb 조합 ──
        if current_tab:
            tab_cfg = cls._tab_keyword_map.get(current_tab, {})
            intent = tab_cfg.get("intent")
            if intent and any(v in msg for v in cls._action_verbs):
                logger.info(f"Tab-context resolved: tab={current_tab} -> {intent}")
                return intent

        # ── 8. 매칭 안 됨 → LLM ──
        return None

    @classmethod
    def extract_domain_from_message(cls, message: str) -> Optional[str]:
        cls._load_keywords()
        msg = message.lower()
        domain_scores: Dict[str, int] = {}
        for keyword, domain in cls._domain_map.items():
            if keyword in msg:
                domain_scores[domain] = domain_scores.get(domain, 0) + 1
        if not domain_scores:
            return None
        return max(domain_scores, key=domain_scores.get)


# ============================================================
# LLM 응답 파서 (Skill JSON 추출)
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
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not brace_match:
        return None, {}

    try:
        parsed = json.loads(brace_match.group(0))
    except json.JSONDecodeError:
        return None, {}

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
