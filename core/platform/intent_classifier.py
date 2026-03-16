"""
core/platform/intent_classifier.py

플랫폼 공통 스킬 내부 Intent 분류기.

2계층 구조:
  1. Fast-Path: 버튼 클릭(정확한 문자열) → 즉시 intent 반환 (LLM 호출 없음)
  2. LLM Classification: 자유 텍스트 → LLM이 {intent, params, confidence} 반환

사용법:
    classifier = SkillIntentClassifier()
    result = classifier.fast_path("problem_definition", "확인", state_summary)
    if result is None:
        result = await classifier.classify(model, "problem_definition", message, state_summary)
    # result.intent, result.params, result.confidence
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[2]
_SKILL_INTENTS_PATH = _BASE / "configs" / "skill_intents.yaml"

# 모듈 레벨 캐시
_skill_intents_config: Optional[Dict] = None


def _load_skill_intents() -> Dict:
    global _skill_intents_config
    if _skill_intents_config is None:
        with open(_SKILL_INTENTS_PATH, "r", encoding="utf-8") as f:
            _skill_intents_config = yaml.safe_load(f) or {}
    return _skill_intents_config


@dataclass
class IntentResult:
    """Intent 분류 결과"""
    intent: str
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source: str = "fast_path"  # "fast_path" | "llm" | "fallback"


class SkillIntentClassifier:
    """스킬 내부 Intent 분류기"""

    # confidence 임계값: 이 이하이면 fallback (modify_general 또는 _llm_smart_apply)
    CONFIDENCE_THRESHOLD = 0.6

    def __init__(self, config: Optional[Dict] = None):
        self._config = config or _load_skill_intents()

    def get_skill_config(self, skill_name: str) -> Optional[Dict]:
        """스킬별 intent 설정 반환."""
        return self._config.get(skill_name)

    # ----------------------------------------------------------
    # 1. Fast-Path: 버튼 클릭 (정확한 문자열 매칭)
    # ----------------------------------------------------------
    def fast_path(
        self, skill_name: str, message: str
    ) -> Optional[IntentResult]:
        """
        정확한 버튼 텍스트 매칭. 매칭 시 IntentResult 반환, 아니면 None.
        """
        skill_config = self._config.get(skill_name)
        if not skill_config:
            return None

        button_actions = skill_config.get("button_actions", {})
        msg_stripped = message.strip()

        # 정확 매칭 (대소문자 무시)
        for button_text, intent_name in button_actions.items():
            if msg_stripped.lower() == button_text.lower():
                logger.info(
                    f"IntentClassifier: fast_path match "
                    f"'{msg_stripped}' → {intent_name} (skill={skill_name})"
                )
                return IntentResult(
                    intent=intent_name,
                    confidence=1.0,
                    source="fast_path",
                )

        return None

    # ----------------------------------------------------------
    # 2. LLM Classification: 자유 텍스트
    # ----------------------------------------------------------
    async def classify(
        self,
        model: Any,
        skill_name: str,
        message: str,
        state_summary: str = "",
        pending_action: str = "",
    ) -> IntentResult:
        """
        LLM을 호출하여 사용자 메시지의 intent를 분류.

        Args:
            model: Gemini model instance
            skill_name: 현재 활성 스킬 이름
            message: 사용자 메시지
            state_summary: 현재 상태 요약 (LLM 컨텍스트)
            pending_action: 진행 중인 보류 작업 설명

        Returns:
            IntentResult with intent, params, confidence
        """
        skill_config = self._config.get(skill_name)
        if not skill_config:
            logger.warning(f"IntentClassifier: no config for skill '{skill_name}'")
            return IntentResult(
                intent="question", confidence=0.3, source="fallback"
            )

        prompt = self._build_prompt(
            skill_name, skill_config, message, state_summary, pending_action
        )

        try:
            import asyncio
            response = await asyncio.to_thread(
                model.generate_content, prompt
            )
            raw = response.text.strip()
            return self._parse_response(raw, skill_name)
        except Exception as e:
            logger.error(f"IntentClassifier LLM call failed: {e}")
            return IntentResult(
                intent="question", confidence=0.3, source="fallback"
            )

    # ----------------------------------------------------------
    # Prompt 생성
    # ----------------------------------------------------------
    def _build_prompt(
        self,
        skill_name: str,
        skill_config: Dict,
        message: str,
        state_summary: str,
        pending_action: str,
    ) -> str:
        intents = skill_config.get("intents", {})

        # intent 목록 포맷팅
        intent_lines = []
        for iname, idef in intents.items():
            desc = idef.get("description", "")
            examples = idef.get("examples", [])
            ex_text = ", ".join(f'"{e}"' for e in examples[:3])
            params = idef.get("extract_params", [])
            param_text = f" (추출 파라미터: {', '.join(params)})" if params else ""
            intent_lines.append(
                f"  - **{iname}**: {desc}{param_text}\n    예시: {ex_text}"
            )

        intent_block = "\n".join(intent_lines)

        pending_text = ""
        if pending_action:
            pending_text = f"\n보류 중인 작업: {pending_action}"

        return f"""당신은 사용자 의도 분류기입니다. 사용자 메시지를 분석하여 가장 적합한 intent를 선택하세요.

## 현재 상태
- 스킬: {skill_name}
{f'- 상태: {state_summary}' if state_summary else ''}
{pending_text}

## 가능한 Intent 목록
{intent_block}

## 분류 규칙
1. 메시지에 수정/변경 의도가 있으면 confirm이 아닌 해당 수정 intent를 선택하세요.
   예: "목적함수를 바꾸고 진행하고 싶습니다" → change_objective (진행=confirm이 아님)
2. 복합 요청(변경 + 진행)은 변경 intent를 우선하세요.
3. 질문/설명 요청은 question을 선택하세요.
4. 불확실하면 confidence를 낮추세요.

## 사용자 메시지
"{message}"

## 응답 형식 (반드시 JSON만 출력)
{{"intent": "intent_name", "params": {{}}, "confidence": 0.0~1.0}}

params에는 extract_params에 해당하는 값만 포함하세요. 추출할 수 없으면 빈 객체 {{}}.
JSON 외에 다른 텍스트를 출력하지 마세요."""

    # ----------------------------------------------------------
    # LLM 응답 파싱
    # ----------------------------------------------------------
    def _parse_response(self, raw: str, skill_name: str) -> IntentResult:
        """LLM 응답에서 JSON을 추출하고 IntentResult로 변환."""
        # JSON 블록 추출 (중첩 브레이스 허용)
        json_match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', raw, re.DOTALL)
        if not json_match:
            logger.warning(f"IntentClassifier: no JSON in response: {raw[:100]}")
            return IntentResult(
                intent="question", confidence=0.3, source="fallback"
            )

        try:
            parsed = json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.warning(f"IntentClassifier: JSON parse failed: {raw[:100]}")
            return IntentResult(
                intent="question", confidence=0.3, source="fallback"
            )

        intent = parsed.get("intent", "question")
        params = parsed.get("params", {})
        confidence = float(parsed.get("confidence", 0.5))

        # intent가 스킬에 정의되어 있는지 검증
        skill_config = self._config.get(skill_name, {})
        valid_intents = set(skill_config.get("intents", {}).keys())
        if intent not in valid_intents:
            logger.warning(
                f"IntentClassifier: unknown intent '{intent}' "
                f"for skill '{skill_name}', falling back to question"
            )
            return IntentResult(
                intent="question",
                params=params,
                confidence=max(confidence * 0.5, 0.2),
                source="llm",
            )

        return IntentResult(
            intent=intent,
            params=params,
            confidence=confidence,
            source="llm",
        )


# ----------------------------------------------------------
# 싱글턴
# ----------------------------------------------------------
_classifier_instance: Optional[SkillIntentClassifier] = None


def get_intent_classifier() -> SkillIntentClassifier:
    """싱글턴 SkillIntentClassifier 인스턴스 반환."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = SkillIntentClassifier()
    return _classifier_instance


# ----------------------------------------------------------
# Intent Log: DB 기록
# ----------------------------------------------------------
def log_intent(
    project_id: Optional[str],
    message: str,
    result: IntentResult,
    skill_name: Optional[str] = None,
    pipeline_stage: Optional[str] = None,
) -> None:
    """Intent 분류 결과를 DB에 비동기적으로 기록 (실패해도 무시)."""
    try:
        from core.database import SessionLocal
        from core.models import IntentLogDB

        db = SessionLocal()
        try:
            pid = int(project_id) if project_id and str(project_id).isdigit() else None
            params_str = json.dumps(result.params, ensure_ascii=False) if result.params else None

            row = IntentLogDB(
                project_id=pid,
                skill_name=skill_name,
                message=message[:2000],  # 메시지 길이 제한
                intent=result.intent,
                confidence=result.confidence,
                source=result.source,
                params_json=params_str,
                pipeline_stage=pipeline_stage,
            )
            db.add(row)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"IntentLog write failed: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"IntentLog import failed: {e}")
