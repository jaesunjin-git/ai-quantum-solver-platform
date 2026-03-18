"""
Ambiguity Detection Engine — 범용 파라미터 모호성 감지 및 질문 생성

도메인에 독립적인 엔진으로, YAML 규칙 파일만으로 동작합니다.
엔진은 파라미터 이름이나 도메인 지식을 하드코딩하지 않습니다.
"""
import logging
import math
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helper: 안전한 표현식 평가
# ─────────────────────────────────────────────

_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "len": len,
    "round": round, "int": int, "float": float, "str": str,
    "bool": bool, "None": None, "True": True, "False": False,
    "math": math,
}


class _DotDict(dict):
    """dict를 속성 접근(.key)과 .get() 모두 지원하는 래퍼.
    YAML 조건식에서 phase1.min_time 형태로 접근 가능."""
    def __getattr__(self, key):
        try:
            val = self[key]
            if isinstance(val, dict) and not isinstance(val, _DotDict):
                return _DotDict(val)
            return val
        except KeyError:
            return None
    def __setattr__(self, key, value):
        self[key] = value


def _safe_eval(expr: str, context: dict) -> Any:
    """제한된 built-in만 허용하는 안전한 eval"""
    try:
        return eval(expr, {"__builtins__": _SAFE_BUILTINS}, context)
    except Exception as e:
        logger.debug(f"Expression eval failed: {expr!r} -> {e}")
        return None


def format_minutes(minutes: Any) -> str:
    """분(float/int)을 HH:MM 형식으로 변환"""
    if minutes is None:
        return "??"
    m = int(float(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


# ─────────────────────────────────────────────
# 질문 데이터 구조
# ─────────────────────────────────────────────

class ClarificationQuestion:
    """사용자에게 보낼 질문 하나"""

    def __init__(self, rule_id: str, question_def: dict, resolved_text: str):
        self.rule_id = rule_id
        self.question_id: str = question_def.get("id", "")
        self.text: str = resolved_text
        self.q_type: str = question_def.get("type", "yes_no")
        self.default = question_def.get("default")
        self.unit: str = question_def.get("unit", "")
        self.param: str = question_def.get("param", "")
        self.transform: str = question_def.get("transform", "")
        self.choices: list = question_def.get("choices", [])
        self.fields: list = question_def.get("fields", [])
        self.on_yes: dict = question_def.get("on_yes", {})
        self.on_no: dict = question_def.get("on_no", {})
        self.dynamic: bool = question_def.get("dynamic", False)
        # dynamic 질문용 메타데이터
        self.dynamic_meta: dict = {}

    def to_dict(self) -> dict:
        d = {
            "rule_id": self.rule_id,
            "question_id": self.question_id,
            "text": self.text,
            "type": self.q_type,
        }
        if self.default is not None:
            d["default"] = self.default
        if self.unit:
            d["unit"] = self.unit
        if self.param:
            d["param"] = self.param
        if self.transform:
            d["transform"] = self.transform
        if self.on_yes:
            d["on_yes"] = self.on_yes
        if self.on_no:
            d["on_no"] = self.on_no
        if self.choices:
            d["choices"] = self.choices
        if self.fields:
            d["fields"] = self.fields
        if self.dynamic_meta:
            d["dynamic_meta"] = self.dynamic_meta
        return d


# ─────────────────────────────────────────────
# AmbiguityDetector 엔진
# ─────────────────────────────────────────────

class AmbiguityDetector:
    """
    YAML 규칙 기반 모호성 감지 엔진.

    사용법:
        detector = AmbiguityDetector("railway")
        questions = detector.detect(parameters, phase1_data, data_facts, phase1_summary)
        # 사용자 답변 수집 후:
        updated_params = detector.apply_answer(question, answer, parameters)
    """

    def __init__(self, domain: str):
        self.domain = domain
        self.rules: dict = {}
        self._load_rules(domain)

    def _load_rules(self, domain: str) -> None:
        """도메인별 ambiguity_rules.yaml 로드"""
        base = Path(__file__).resolve().parent.parent.parent
        rules_path = base / "knowledge" / "domains" / domain / "ambiguity_rules.yaml"
        if not rules_path.exists():
            logger.info(f"No ambiguity rules for domain '{domain}': {rules_path}")
            return
        try:
            with open(rules_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self.rules = data.get("rules", {})
            logger.info(f"Loaded {len(self.rules)} ambiguity rules for '{domain}'")
        except Exception as e:
            logger.warning(f"Failed to load ambiguity rules: {e}")

    # ─────────────────────────────────────────
    # 감지 (detect)
    # ─────────────────────────────────────────

    def detect(
        self,
        parameters: dict,
        phase1_data: dict,
        data_facts: Optional[dict] = None,
        phase1_summary: Optional[dict] = None,
        constraints: Optional[dict] = None,
        answered_ids: Optional[set] = None,
    ) -> list[ClarificationQuestion]:
        """
        모든 규칙을 평가하여 질문 목록 반환.

        Args:
            parameters: _collect_parameters() 결과
            phase1_data: _load_phase1_data() 결과
            data_facts: state.data_facts
            phase1_summary: state.phase1_summary
            constraints: {"hard": {...}, "soft": {...}}
            answered_ids: 이미 답변된 question_id 집합 (중복 질문 방지)

        Returns:
            ClarificationQuestion 리스트 (importance 순 정렬)
        """
        if not self.rules:
            return []

        answered = answered_ids or set()
        context = self._build_context(parameters, phase1_data, data_facts, phase1_summary)
        questions: list[ClarificationQuestion] = []
        suppressed_rules: set[str] = set()

        # importance 순서: critical > high > low
        importance_order = {"critical": 0, "high": 1, "low": 2}

        for rule_id, rule_def in self.rules.items():
            # suppressed_by 체크
            suppressed_by = rule_def.get("suppressed_by", "")
            if suppressed_by and self._is_suppressed(suppressed_by, answered):
                continue

            trigger = rule_def.get("trigger", {})
            trigger_type = trigger.get("type", "condition")

            triggered = False
            dynamic_items = []

            if trigger_type == "condition":
                # 일반 조건식 평가
                condition = trigger.get("condition", "")
                if condition and _safe_eval(condition, context):
                    triggered = True

            elif trigger_type == "param_range_check":
                # 모든 파라미터의 typical_range 검사 (범용)
                dynamic_items = self._check_param_ranges(parameters, constraints)
                triggered = len(dynamic_items) > 0

            elif trigger_type == "default_value_check":
                # 지정된 파라미터 중 source가 data가 아닌 것 검사
                target_params = trigger.get("target_params", [])
                dynamic_items = self._check_default_values(parameters, target_params)
                triggered = len(dynamic_items) > 0

            if not triggered:
                continue

            # 질문 생성
            for q_def in rule_def.get("questions", []):
                qid = f"{rule_id}.{q_def.get('id', '')}"
                if qid in answered:
                    continue

                # ── 이미 데이터에 값이 있는 파라미터는 질문 스킵 ──
                # param 필드 또는 on_yes/on_no.set_params의 키가 parameters에 존재하면
                # 데이터에서 이미 추출된 값이므로 사용자에게 다시 묻지 않음
                _skip_existing = False
                _q_param = q_def.get("param", "")
                if _q_param and _q_param in parameters:
                    _existing_val = parameters[_q_param]
                    if isinstance(_existing_val, dict):
                        _existing_val = _existing_val.get("value")
                    if _existing_val is not None:
                        logger.info(f"Ambiguity skip: '{qid}' — param '{_q_param}' already has value {_existing_val} from data")
                        answered.add(qid)
                        _skip_existing = True
                if _skip_existing:
                    continue

                if q_def.get("dynamic") and dynamic_items:
                    # dynamic 질문: 해당하는 항목마다 질문 생성
                    for item in dynamic_items:
                        item_qid = f"{qid}:{item['param_name']}"
                        if item_qid in answered:
                            continue
                        resolved_text = self._resolve_text(q_def.get("text", ""), context, item)
                        q = ClarificationQuestion(rule_id, q_def, resolved_text)
                        q.question_id = item_qid
                        q.dynamic_meta = item
                        questions.append(q)
                else:
                    resolved_text = self._resolve_text(q_def.get("text", ""), context)
                    # text_vars 표현식 기반 변수 해석 (예: format_minutes(phase1.min_time))
                    text_vars = q_def.get("text_vars", {})
                    if text_vars:
                        for var_name, expr in text_vars.items():
                            value = _safe_eval(expr, context)
                            resolved_text = resolved_text.replace(
                                f"{{{var_name}}}", str(value) if value is not None else "??"
                            )
                    q = ClarificationQuestion(rule_id, q_def, resolved_text)
                    q.question_id = qid
                    questions.append(q)

        # importance 순 정렬
        def sort_key(q: ClarificationQuestion):
            rule = self.rules.get(q.rule_id, {})
            imp = rule.get("importance", "low")
            return importance_order.get(imp, 2)

        questions.sort(key=sort_key)
        return questions

    # ─────────────────────────────────────────
    # 답변 적용 (apply_answer)
    # ─────────────────────────────────────────

    def apply_answer(
        self,
        question: ClarificationQuestion,
        answer: Any,
        parameters: dict,
    ) -> dict:
        """
        사용자 답변을 파라미터에 반영.

        Args:
            question: 질문 객체
            answer: 사용자 답변 (bool, number, str, dict 등)
            parameters: 현재 파라미터 딕셔너리 (in-place 수정)

        Returns:
            {
                "updated_params": dict,    # 변경된 파라미터들
                "follow_up": list[str],    # 추가 질문 ID (있으면)
            }
        """
        result = {"updated_params": {}, "follow_up": []}

        if question.q_type == "yes_no":
            branch = question.on_yes if answer else question.on_no
            # set_params 적용
            for pname, pval in branch.get("set_params", {}).items():
                parameters[pname] = pval
                result["updated_params"][pname] = pval
            # follow_up
            result["follow_up"] = branch.get("follow_up", [])

        elif question.q_type == "numeric":
            value = float(answer) if answer is not None else question.default
            # transform 적용
            if question.transform and value is not None:
                value = _safe_eval(question.transform, {"value": value})
            if question.param:
                parameters[question.param] = {
                    "value": value,
                    "source": "user_clarification",
                    "unit": question.unit,
                }
                result["updated_params"][question.param] = parameters[question.param]

        elif question.q_type == "numeric_optional":
            if answer is not None and answer != "":
                value = float(answer)
                if question.transform and value is not None:
                    value = _safe_eval(question.transform, {"value": value})
                if question.param:
                    parameters[question.param] = {
                        "value": value,
                        "source": "user_clarification",
                        "unit": question.unit,
                    }
                    result["updated_params"][question.param] = parameters[question.param]

        elif question.q_type == "multi_input":
            # answer: {"field_id": value, ...}
            if isinstance(answer, dict):
                for field in question.fields:
                    fid = field.get("id", "")
                    if fid in answer and answer[fid] is not None:
                        parameters[fid] = {
                            "value": float(answer[fid]),
                            "source": "user_clarification",
                        }
                        result["updated_params"][fid] = parameters[fid]

        elif question.q_type == "choice":
            # answer: choice_id
            for choice in question.choices:
                if choice.get("id") == answer:
                    for pname, pval in choice.get("set_params", {}).items():
                        parameters[pname] = pval
                        result["updated_params"][pname] = pval
                    if choice.get("action") == "request_input":
                        result["follow_up"] = [f"manual:{choice.get('param', '')}"]
                    break

        # dynamic 질문의 on_no → request_input
        if question.dynamic and question.q_type == "yes_no" and not answer:
            on_no = question.on_no
            if on_no.get("action") == "request_input":
                param_name = question.dynamic_meta.get("param_name", "")
                result["follow_up"] = [f"manual:{param_name}"]

        return result

    # ─────────────────────────────────────────
    # 내부 헬퍼
    # ─────────────────────────────────────────

    def _build_context(
        self,
        parameters: dict,
        phase1_data: dict,
        data_facts: Optional[dict],
        phase1_summary: Optional[dict],
    ) -> dict:
        """조건식 평가용 컨텍스트 빌드. 모든 dict를 _DotDict로 래핑하여 속성 접근 지원."""
        ctx = {
            "params": _DotDict(parameters or {}),
            "phase1": _DotDict(phase1_data or {}),
            "facts": _DotDict(data_facts or {}),
            "phase1_summary": _DotDict(phase1_summary or {}),
            "format_minutes": format_minutes,
        }
        return ctx

    def _resolve_text(self, template: str, context: dict, extra_vars: Optional[dict] = None) -> str:
        """질문 텍스트의 변수를 해석"""
        text = template
        # extra_vars (dynamic 질문용)
        if extra_vars:
            for k, v in extra_vars.items():
                text = text.replace(f"{{{k}}}", str(v))

        # text_vars는 질문 정의에 있을 수 있으나, 여기선 context 기반 interpolation
        # {variable_name} 패턴 찾아서 치환
        import re
        placeholders = re.findall(r'\{(\w+)\}', text)
        for ph in placeholders:
            # context에서 직접 검색
            val = context.get(ph)
            if val is None and extra_vars:
                val = extra_vars.get(ph)
            if val is not None:
                text = text.replace(f"{{{ph}}}", str(val))

        # text_vars 처리 (표현식 기반)
        # 이 부분은 rule 정의의 text_vars에서 처리
        return text

    def resolve_text_vars(self, question_def: dict, context: dict) -> str:
        """text_vars를 포함한 질문 텍스트 완전 해석"""
        text = question_def.get("text", "")
        text_vars = question_def.get("text_vars", {})
        for var_name, expr in text_vars.items():
            value = _safe_eval(expr, context)
            text = text.replace(f"{{{var_name}}}", str(value) if value is not None else "??")
        return text

    def _is_suppressed(self, suppressed_by: str, answered: set) -> bool:
        """suppressed_by 조건 확인 (예: "rule.question:yes")"""
        parts = suppressed_by.split(":")
        if len(parts) != 2:
            return False
        qid_pattern, expected_answer = parts
        # answered set에 "rule.question" + answer가 있으면 suppressed
        return f"{qid_pattern}={expected_answer}" in answered

    def _check_param_ranges(self, parameters: dict, constraints: Optional[dict]) -> list[dict]:
        """모든 파라미터의 typical_range를 검사하여 범위 이탈 항목 반환"""
        out_of_range = []
        if not constraints:
            return out_of_range

        for cat in ["hard", "soft"]:
            for cname, cinfo in constraints.get(cat, {}).items():
                for pname, pdata in cinfo.get("parameters", {}).items() if isinstance(cinfo.get("parameters"), dict) else []:
                    typical = pdata.get("typical_range", [])
                    if len(typical) < 2:
                        continue
                    # 현재 파라미터 값
                    param_val = parameters.get(pname, {})
                    if isinstance(param_val, dict):
                        val = param_val.get("value")
                    else:
                        val = param_val
                    if val is None:
                        continue
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        continue
                    range_min, range_max = float(typical[0]), float(typical[1])
                    if val < range_min or val > range_max:
                        unit = ""
                        if isinstance(param_val, dict):
                            unit = param_val.get("unit", "")
                        out_of_range.append({
                            "param_name": pname,
                            "value": val,
                            "range_min": range_min,
                            "range_max": range_max,
                            "unit": unit,
                        })
        return out_of_range

    def _check_default_values(self, parameters: dict, target_params: list) -> list[dict]:
        """지정된 파라미터 중 source가 data가 아닌 것 반환"""
        defaults = []
        for pname in target_params:
            pdata = parameters.get(pname, {})
            if not isinstance(pdata, dict):
                continue
            source = pdata.get("source", "")
            # data에서 추출된 게 아니면 확인 대상
            if source and "parameters.csv" not in source and source != "user_clarification":
                defaults.append({
                    "param_name": pname,
                    "value": pdata.get("value", ""),
                    "unit": pdata.get("unit", ""),
                    "source": source,
                })
        return defaults
