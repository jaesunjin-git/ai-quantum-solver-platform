"""
core/platform/stage_manager.py

파이프라인 단계 전이 관리 — 순방향 진행 / 역방향 복귀를 YAML 선언 기반으로 처리.

사용법:
    manager = StageManager()                    # configs/pipeline.yaml 자동 로드
    can, redirect = manager.can_enter(state, "MATH_MODEL")
    if not can:
        # redirect 단계로 이동
    manager.prepare_reentry(state, "PROBLEM_DEFINITION")  # 역방향이면 자동 초기화
"""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[2]
_PIPELINE_PATH = _BASE / "configs" / "pipeline.yaml"

# 모듈 레벨 캐시
_pipeline_config: Optional[Dict] = None


def _load_pipeline_config() -> Dict:
    global _pipeline_config
    if _pipeline_config is None:
        with open(_PIPELINE_PATH, "r", encoding="utf-8") as f:
            _pipeline_config = yaml.safe_load(f)
    return _pipeline_config


class StageManager:
    """파이프라인 단계 전이 관리자"""

    def __init__(self, config: Optional[Dict] = None):
        raw = config or _load_pipeline_config()
        self.stages: Dict[str, Dict] = raw.get("stages", {})

        # intent_code → stage_name 역인덱스
        self._intent_to_stage: Dict[str, str] = {}
        for stage_name, stage_def in self.stages.items():
            for intent_code in stage_def.get("intent_codes", []):
                self._intent_to_stage[intent_code] = stage_name

        # stage_name → order 인덱스
        self._stage_order: Dict[str, int] = {
            name: sdef["order"] for name, sdef in self.stages.items()
        }

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def stage_for_intent(self, intent: str) -> Optional[str]:
        """intent 코드 → 해당 파이프라인 단계명. 매핑 없으면 None."""
        return self._intent_to_stage.get(intent)

    def current_stage(self, state: Any) -> Optional[str]:
        """현재 state에서 가장 높은 완료 단계 바로 다음(= 현재 진행 중) 단계 반환."""
        for stage_name in sorted(
            self.stages, key=lambda s: self._stage_order[s]
        ):
            flag = self.stages[stage_name]["state_flag"]
            if not getattr(state, flag, False):
                return stage_name
        return None  # 모든 단계 완료

    def current_order(self, state: Any) -> int:
        """현재 진행 중인 단계의 order. 모든 단계 완료 시 max_order + 1."""
        stage = self.current_stage(state)
        if stage is None:
            return max(self._stage_order.values(), default=0) + 1
        return self._stage_order[stage]

    def can_enter(
        self, state: Any, intent: str
    ) -> Tuple[bool, Optional[str]]:
        """
        해당 intent의 단계에 진입 가능한지 확인.

        Returns:
            (True, target_stage)   — 진입 가능
            (False, redirect_stage) — 필수 조건 미충족, redirect_stage로 가야 함
            (True, None)           — 파이프라인 외 intent (RESET, GUIDE 등)
        """
        target_stage = self.stage_for_intent(intent)
        if target_stage is None:
            # 파이프라인 외 intent (RESET, GUIDE, ANSWER, GENERAL 등)
            return True, None

        stage_def = self.stages[target_stage]
        for req_flag in stage_def.get("requires", []):
            if not getattr(state, req_flag, False):
                # 이 플래그를 완료하는 단계를 찾아 리다이렉트
                redirect = self._find_stage_for_flag(req_flag)
                logger.info(
                    f"StageManager: {intent} → {target_stage} blocked "
                    f"(missing {req_flag}), redirect to {redirect}"
                )
                return False, redirect

        return True, target_stage

    def is_backward(self, state: Any, intent: str) -> bool:
        """intent가 가리키는 단계가 현재 진행 단계보다 이전(역방향)인지 판단."""
        target_stage = self.stage_for_intent(intent)
        if target_stage is None:
            return False
        target_order = self._stage_order.get(target_stage, 0)
        cur_order = self.current_order(state)
        return target_order < cur_order

    def prepare_reentry(self, state: Any, intent: str) -> List[str]:
        """
        역방향 복귀 시 후속 단계 상태를 초기화.

        Returns:
            초기화된 필드 이름 목록 (로깅/디버그용)
        """
        target_stage = self.stage_for_intent(intent)
        if target_stage is None:
            return []

        if not self.is_backward(state, intent):
            return []  # 순방향이면 초기화 불필요

        reset_fields = self.stages[target_stage].get("resets_on_reentry", [])
        reset_done: List[str] = []

        for field_name in reset_fields:
            if not hasattr(state, field_name):
                continue
            current_val = getattr(state, field_name)
            if isinstance(current_val, bool):
                if current_val:
                    setattr(state, field_name, False)
                    reset_done.append(field_name)
            else:
                if current_val is not None:
                    setattr(state, field_name, None)
                    reset_done.append(field_name)

        if reset_done:
            logger.info(
                f"StageManager: reentry to {target_stage} — "
                f"reset {len(reset_done)} fields: {reset_done[:5]}..."
            )

        return reset_done

    def get_stage_info(self, stage_name: str) -> Optional[Dict]:
        """단계 정의 반환."""
        return self.stages.get(stage_name)

    def get_pipeline_phase_text(self, state: Any) -> str:
        """현재 파이프라인 단계를 사람이 읽기 쉬운 문자열로 반환."""
        if not getattr(state, "file_uploaded", False):
            return "Phase 0: 파일 미업로드 — 데이터 파일 업로드 대기 중"

        phase_texts = {
            "analysis": "Phase 1: 데이터 분석 단계",
            "structural_normalization": "Phase 1.5: 구조 정규화 단계",
            "problem_definition": "Phase 2: 문제 정의 단계",
            "data_normalization": "Phase 3: 데이터 정규화 단계",
            "math_model": "Phase 4: 수학 모델 단계",
            "pre_decision": "Phase 5: 솔버 추천 단계",
            "optimization": "Phase 6: 최적화 실행 단계",
        }
        stage = self.current_stage(state)
        if stage is None:
            return "Phase 7: 완료 — 최적화 실행 완료, 결과 확인 가능"
        return phase_texts.get(stage, f"Phase ?: {stage}")

    # ----------------------------------------------------------
    # Internal
    # ----------------------------------------------------------

    def _find_stage_for_flag(self, flag: str) -> Optional[str]:
        """특정 state_flag를 완료하는 단계를 찾는다."""
        for stage_name, stage_def in self.stages.items():
            if stage_def["state_flag"] == flag:
                return stage_name
        return None


# ----------------------------------------------------------
# 싱글턴 인스턴스 (모듈 레벨)
# ----------------------------------------------------------
_manager_instance: Optional[StageManager] = None


def get_stage_manager() -> StageManager:
    """싱글턴 StageManager 인스턴스 반환."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = StageManager()
    return _manager_instance
