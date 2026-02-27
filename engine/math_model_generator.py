# ============================================================
# engine/model_generator.py — v1.0
# ============================================================
# 역할: LLM에게 데이터 요약과 분석 리포트를 보내고,
#       범용 수학 모델 JSON(Intermediate Representation)을 생성받는다.
#       이 JSON은 솔버에 독립적이며, OR-Tools/D-Wave 변환기의 입력이 된다.
# ============================================================

from __future__ import annotations

import json
import logging
import asyncio
from typing import Dict, Any, Optional

import google.generativeai as genai
from core.config import settings
from utils.prompt_loader import load_yaml_prompt, load_schema, get_constraint_schema_text

logger = logging.getLogger(__name__)

# ── LLM 초기화 ──
_model = None
try:
    genai.configure(api_key=settings.GOOGLE_API_KEY)
    _model = genai.GenerativeModel(
        model_name=settings.MODEL_MODELING,
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            max_output_tokens=8192,
        ),
    )
    logger.info(f"ModelGenerator LLM initialized: {settings.MODEL_MODELING} (temperature=0)")
except Exception as e:
    logger.warning(f"ModelGenerator LLM init failed: {e}")


# ============================================================
# JSON 스키마 정의 (LLM에게 보여줄 빈 양식)
# ============================================================
# 수학 모델 JSON 스키마 - prompts/schemas/constraint_schema.yaml에서 제약 스키마 로드
def _get_model_schema() -> str:
    config = load_yaml_prompt("crew", "math_model")
    if not config:
        return "{}"
    # 기본 모델 스키마 (sets, parameters, variables, objective, metadata)
    schema = {
        "model_version": "1.0",
        "problem_name": "<problem name>",
        "domain": "<domain key>",
        "sets": [{"id": "I", "name": "<name>", "description": "<desc>", "source_column": "<col>", "source_file": "<file>"}],
        "parameters": [{"id": "<id>", "name": "<name>", "type": "<numeric|boolean|constant|categorical>", "source_column": "<col>", "source_file": "<file>", "value": "", "unit": "<unit>"}],
        "variables": [{"id": "x", "name": "<name>", "type": "<binary|integer|continuous>", "indices": ["I", "J"], "description": "<decision variable description>", "lower_bound": 0, "upper_bound": 1}],
        "objective": {"type": "<minimize|maximize>", "description": "<desc>", "expression": "<expr>", "alternatives": []},
        "constraints": [{"name": "<name>", "description": "<desc>", "for_each": "<i in I>", "lhs": {}, "operator": "<==|<=|>=|<|>|!=>", "rhs": {}, "priority": "<hard|soft>", "weight": 0, "expression": "<Python expr>"}],
        "metadata": {"estimated_variable_count": 0, "estimated_constraint_count": 0, "variable_types_used": [], "data_files_required": [], "assumptions": []}
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


# ============================================================
# 팩트 포맷 헬퍼 함수
# ============================================================
def _format_facts_for_model(facts: Optional[dict]) -> str:
    """팩트 데이터를 수학 모델 프롬프트용 텍스트로 변환"""
    if not facts:
        return "팩트 데이터 없음"

    lines = []
    lines.append("아래 수치는 코드로 계산된 정확한 값입니다. 반드시 이 값을 사용하세요.")
    lines.append("")

    # 파일별 레코드 수
    for f in facts.get("files", []):
        lines.append(f"파일: {f['name']} → 레코드 수: {f.get('records', 0)}")

    # 시트별 행/열 수
    for filename, sheets in facts.get("sheet_info", {}).items():
        for sheet_name, info in sheets.items():
            cols = info.get("column_names", [])
            lines.append(f"  [{filename} → {sheet_name}] 행: {info['rows']}, 열: {info['cols']}, 컬럼: {cols[:10]}")

    # 고유값 수 (집합 크기 산정 근거)
    unique = facts.get("unique_counts", {})
    if unique:
        lines.append("")
        lines.append("주요 컬럼별 고유값 수 (sets 크기 산정에 반드시 이 값을 사용):")
        for key, count in sorted(unique.items(), key=lambda x: -x[1])[:30]:
            lines.append(f"  {key}: {count}개")

    lines.append("")
    lines.append("중요: estimated_variable_count는 위 고유값 수를 기반으로 정확히 계산하세요.")
    lines.append("예: 승무원 96명 × 사업 25개 = binary 변수 2400개")

    return "\n".join(lines)

# ============================================================
# 프롬프트 빌더
# ============================================================
def _build_modeling_prompt(
    csv_summary: str,
    analysis_report: str,
    domain: str,
    user_objective: Optional[str] = None,
    data_facts: Optional[dict] = None,
) -> str:
    """LLM에게 수학 모델 JSON 생성을 요청하는 프롬프트를 조립 (YAML 기반)"""

    # YAML에서 프롬프트 설정 로드
    config = load_yaml_prompt("crew", "math_model")
    system = config.get("system", "당신은 최적화 문제의 수학적 모델링 전문가입니다.")
    rules = config.get("rules", [])
    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

    # 제약 스키마 텍스트
    constraint_schema_text = get_constraint_schema_text()

    # 모델 스키마
    model_schema = _get_model_schema()

    # 목적함수 지시
    if user_objective:
        objective_instruction = (
            "[사용자 지정 목적함수]\n"
            f"사용자가 다음과 같은 최적화 목표를 요청했습니다: \"{user_objective}\"\n"
            "이 목표를 objective의 기본값으로 설정하고, 다른 목표는 alternatives에 넣으세요."
        )
    else:
        objective_instruction = (
            "[목적함수 추론]\n"
            "사용자가 명시적으로 목적함수를 지정하지 않았습니다.\n"
            "데이터와 도메인 특성을 분석하여 가장 적절한 목적함수를 추론하세요."
        )

    # 프롬프트 조립
    prompt = f"""{system}

중요 규칙:
{rules_text}

출력 JSON 스키마:
{model_schema}

제약조건 작성 형식 (반드시 이 형식을 따르세요):
{constraint_schema_text}

{objective_instruction}

[검증된 데이터 팩트]
{_format_facts_for_model(data_facts)}

[데이터 요약]
{csv_summary[:4000]}

[분석 리포트]
{analysis_report[:3000]}

[도메인]
{domain}

위 스키마의 모든 필드를 채워서 순수 JSON으로 반환하세요."""
    return prompt


# ============================================================
# JSON 파싱 유틸
# ============================================================

def _repair_truncated_json(text: str) -> Optional[str]:
    """
    잘린(truncated) JSON을 복구한다.
    괄호/대괄호 스택을 추적하여 열린 것들을 역순으로 닫는다.
    """
    stack = []
    in_string = False
    escape = False
    last_complete_pos = 0

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
                last_complete_pos = i + 1
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
                last_complete_pos = i + 1

    if not stack:
        return None  # already balanced

    # 마지막으로 완전했던 위치까지 자르기
    truncated = text[:last_complete_pos] if last_complete_pos > 0 else text
    truncated = truncated.rstrip()
    if truncated.endswith(","):
        truncated = truncated[:-1]

    # 남은 열린 괄호를 다시 계산
    stack2 = []
    in_str2 = False
    esc2 = False
    for ch in truncated:
        if esc2:
            esc2 = False
            continue
        if ch == "\\":
            esc2 = True
            continue
        if ch == '"':
            in_str2 = not in_str2
            continue
        if in_str2:
            continue
        if ch in ("{", "["):
            stack2.append(ch)
        elif ch == "}" and stack2 and stack2[-1] == "{":
            stack2.pop()
        elif ch == "]" and stack2 and stack2[-1] == "[":
            stack2.pop()

    # 열린 괄호 닫기
    closing = ""
    for opener in reversed(stack2):
        closing += "}" if opener == "{" else "]"

    result = truncated + closing
    logger.info(f"Repaired truncated JSON: cut {len(text) - len(truncated)} chars, added {len(closing)} closing brackets")
    return result


def _parse_model_json(text: str) -> Optional[Dict[str, Any]]:
    """LLM 응답에서 JSON을 추출하고 파싱 (자동 수정 포함)"""
    import re

    # 마크다운 코드블록 제거
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end])

    # JSON 부분만 추출
    try:
        brace_start = cleaned.index("{")
        brace_end = cleaned.rindex("}") + 1
        cleaned = cleaned[brace_start:brace_end]
    except ValueError:
        logger.error("No JSON braces found in LLM response")
        return None

    # null -> 0 또는 "" 로 치환
    cleaned = re.sub(r':\s*null', ': 0', cleaned)

    # 1차 시도: 그대로 파싱
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 1.5차 시도: JSON 내 수식 표현을 계산된 값으로 치환
    def eval_math_expr(match):
        expr = match.group(1)
        try:
            if re.match(r'^[\d\s\+\-\*\/\(\)\.]+$', expr):
                result = eval(expr)
                if isinstance(result, float) and result == int(result):
                    return ": " + str(int(result))
                return ": " + str(result)
        except:
            pass
        return match.group(0)

    fixed_math = re.sub(
        r':\s*(\d+\.?\d*\s*[\+\-\*\/]\s*\d+\.?\d*(?:\s*[\+\-\*\/]\s*\d+\.?\d*)*)',
        eval_math_expr,
        cleaned
    )
    try:
        return json.loads(fixed_math)
    except json.JSONDecodeError:
        cleaned = fixed_math

    # 2차 시도: 일반적인 JSON 오류 자동 수정
    fixed = cleaned
    # trailing comma 제거: ,} 또는 ,]
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)
    # 줄바꿈 후 쉼표 누락 수정: }\n" 또는 ]\n" 패턴
    fixed = re.sub(r'}\s*\n\s*"', '},\n"', fixed)
    fixed = re.sub(r']\s*\n\s*"', '],\n"', fixed)
    # 문자열 끝 후 쉼표 누락: "value"\n"key"
    fixed = re.sub(r'"\s*\n\s*"', '",\n"', fixed)
    # 숫자 후 쉼표 누락: 123\n"key"
    fixed = re.sub(r'(\d)\s*\n\s*"', r'\1,\n"', fixed)
    # true/false/null 후 쉼표 누락
    fixed = re.sub(r'(true|false|null)\s*\n\s*"', r'\1,\n"', fixed)

    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass


    # 2.5차 시도: 잘린 JSON 복구 (괄호 균형 맞추기)
    repaired = _repair_truncated_json(fixed)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 3차 시도: 줄 단위로 문제 줄 제거
    repair_src = repaired if repaired else fixed
    lines = repair_src.split("\n")
    for attempt in range(min(5, len(lines))):
        try:
            return json.loads("\n".join(lines))
        except json.JSONDecodeError as e:
            error_line = e.lineno - 1 if e.lineno else 0
            if 0 <= error_line < len(lines):
                logger.warning(f"Removing problematic line {error_line}: {lines[error_line][:80]}")
                lines.pop(error_line)
            else:
                break

    logger.error("All JSON parse attempts failed")
    return None

# ============================================================
# 모델 검증
# ============================================================
def validate_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """생성된 수학 모델의 기본 구조를 검증하고 metadata를 보강"""
    errors = []
    warnings = []

    # 필수 필드 확인
    required_keys = ["sets", "parameters", "variables", "objective", "constraints"]
    for key in required_keys:
        if key not in model:
            errors.append(f"필수 필드 '{key}'가 누락되었습니다.")
        elif not model[key]:
            warnings.append(f"'{key}'가 비어 있습니다.")

    # 변수 타입 확인 및 자동 보정
    valid_var_types = {"binary", "integer", "continuous"}
    type_aliases = {"numeric": "continuous", "float": "continuous", "real": "continuous", "bool": "binary", "boolean": "binary", "int": "integer", "categorical": "integer"}
    for var in model.get("variables", []):
        vtype = var.get("type", "").lower().strip()
        if vtype in type_aliases:
            var["type"] = type_aliases[vtype]
            warnings.append(f"변수 '{var.get('id')}'의 type '{vtype}'을 '{type_aliases[vtype]}'으로 자동 변환했습니다.")
        elif vtype not in valid_var_types:
            errors.append(f"변수 '{var.get('id')}'의 type이 유효하지 않습니다: {var.get('type')}")

    # 제약 카테고리 확인 (priority -> category 호환 처리)
    valid_categories = {"hard", "soft"}
    for con in model.get("constraints", []):
        # 새 스키마: priority 필드 -> category로 매핑
        if con.get("category") is None and con.get("priority"):
            con["category"] = con["priority"]
        if con.get("category") not in valid_categories:
            con["category"] = "hard"  # 기본값
            warnings.append(f"제약 '{con.get('name', con.get('name', con.get('id')))}'의 category를 hard로 설정")

    # 집합 ID와 변수 indices 교차 검증
    set_ids = {s.get("id") for s in model.get("sets", [])}
    for var in model.get("variables", []):
        for idx in var.get("indices", []):
            if idx not in set_ids:
                warnings.append(f"변수 '{var.get('id')}'의 index '{idx}'가 sets에 정의되지 않았습니다.")

    # metadata 자동 보강
    if "metadata" not in model:
        model["metadata"] = {}

    meta = model["metadata"]

    # 변수 개수 추정: sets 크기를 모르므로 LLM 추정값 유지
    if "estimated_variable_count" not in meta:
        meta["estimated_variable_count"] = 0

    # 제약 개수
    meta["estimated_constraint_count"] = len(model.get("constraints", []))

    # 사용된 변수 타입
    meta["variable_types_used"] = list({
        v.get("type") for v in model.get("variables", []) if v.get("type")
    })

    # 필요 파일 목록
    files = set()
    for s in model.get("sets", []):
        if s.get("source_file"):
            files.add(s["source_file"])
    for p in model.get("parameters", []):
        if p.get("source_file"):
            files.add(p["source_file"])
    meta["data_files_required"] = list(files)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "model": model,
    }


# ============================================================
# 메인 함수: 수학 모델 생성
# ============================================================
async def generate_math_model(
    csv_summary: str,
    analysis_report: str,
    domain: str,
    user_objective: Optional[str] = None,
    data_facts: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    LLM을 호출하여 수학 모델 JSON을 생성하고 검증한다.

    Returns:
        {
            "success": bool,
            "model": { ... } or None,
            "validation": { "valid": bool, "errors": [...], "warnings": [...] },
            "error": str or None
        }
    """
    if not _model:
        return {
            "success": False,
            "model": None,
            "validation": None,
            "error": "LLM 모델이 초기화되지 않았습니다.",
        }

    try:
        prompt = _build_modeling_prompt(
            csv_summary=csv_summary,
            analysis_report=analysis_report,
            domain=domain,
            user_objective=user_objective,
            data_facts=data_facts,
        )

        logger.info(f"Generating math model for domain={domain}, prompt_len={len(prompt)}")

        response = await asyncio.to_thread(
            _model.generate_content, prompt
        )

        raw_text = response.text.strip()
        logger.info(f"LLM math model response length: {len(raw_text)}")
        logger.info(f"LLM math model raw response (first 500): {raw_text[:500]}")

        # JSON 파싱
        model = _parse_model_json(raw_text)
        if not model:
            return {
                "success": False,
                "model": None,
                "validation": None,
                "error": "LLM 응답에서 유효한 JSON을 추출할 수 없습니다.",
                "raw_response": raw_text[:500],
            }

        # 검증
        validation = validate_model(model)
        
        # 검증 결과 로깅 추가
        # 디버그: 모델 구조 상세 로깅
        _vars = model.get("variables", [])
        _cons = model.get("constraints", [])
        logger.info(f"Model variables ({len(_vars)}):")
        for _v in _vars:
            logger.info(f"  var: id={_v.get('id')}, type={_v.get('type')}, indices={_v.get('indices')}, desc={_v.get('description','')[:60]}")
        logger.info(f"Model constraints ({len(_cons)}):")
        for _c in _cons[:5]:
            logger.info(f"  con: name={_c.get('name', _c.get('id'))}, lhs={str(_c.get('lhs',''))[:80]}, op={_c.get('operator')}, rhs={str(_c.get('rhs',''))[:80]}")
        if len(_cons) > 5:
            logger.info(f"  ... and {len(_cons)-5} more constraints")

        logger.info(f"Validation result: valid={validation['valid']}, errors={validation['errors']}, warnings={validation['warnings']}")

        return {
            "success": validation["valid"],
            "model": validation["model"],
            "validation": {
                "valid": validation["valid"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            },
            "error": None if validation["valid"] else "모델 검증에서 문제가 발견되었습니다.",
        }

    except Exception as e:
        logger.error(f"Math model generation failed: {e}", exc_info=True)
        return {
            "success": False,
            "model": None,
            "validation": None,
            "error": f"수학 모델 생성 중 오류: {str(e)}",
        }


# ============================================================
# 유틸: 모델 요약 (사용자에게 보여줄 텍스트 생성)
# ============================================================
def summarize_model(model: Dict[str, Any]) -> str:
    """수학 모델 JSON을 사용자가 이해할 수 있는 한국어 요약으로 변환"""
    lines = []

    name = model.get("problem_name", "최적화 문제")
    lines.append(f"## 📐 수학 모델: {name}\n")

    # 집합
    sets = model.get("sets", [])
    if sets:
        lines.append("### 📋 집합 (Sets)")
        lines.append("| ID | 이름 | 설명 | 데이터 출처 |")
        lines.append("|:---|:-----|:-----|:-----------|")
        for s in sets:
            source = f"{s.get('source_file', '')} → {s.get('source_column', '')}"
            lines.append(f"| {s.get('id', '')} | {s.get('name', '')} | {s.get('description', '')} | {source} |")
        lines.append("")

    # 변수
    variables = model.get("variables", [])
    if variables:
        lines.append("### 🔤 의사결정 변수")
        lines.append("| ID | 이름 | 타입 | 인덱스 | 설명 |")
        lines.append("|:---|:-----|:-----|:-------|:-----|")
        for v in variables:
            indices = " × ".join(v.get("indices", []))
            lines.append(f"| {v.get('id', '')} | {v.get('name', '')} | {v.get('type', '')} | {indices} | {v.get('description', '')} |")
        lines.append("")

    # 목적함수
    obj = model.get("objective", {})
    if obj:
        lines.append("### 🎯 목적함수")
        lines.append(f"**{obj.get('type', 'minimize')}**: {obj.get('description', '')}")
        lines.append(f"```\n{obj.get('expression', '')}\n```")
        alts = obj.get("alternatives", [])
        if alts:
            lines.append("\n**대안 목적함수:**")
            for alt in alts:
                lines.append(f"- {alt.get('type', '')}: {alt.get('description', '')}")
        lines.append("")

    # 제약조건
    constraints = model.get("constraints", [])
    if constraints:
        hard = [c for c in constraints if c.get("category", c.get("priority")) == "hard"]
        soft = [c for c in constraints if c.get("category", c.get("priority")) == "soft"]

        if hard:
            lines.append("### 🔒 Hard 제약 (필수)")
            for c in hard:
                lines.append(f"- **{c.get('name', '')}**: {c.get('description', '')}")
                lines.append(f"  `{c.get('expression', '')}` (∀ {c.get('for_each', '')})")

        if soft:
            lines.append("\n### ⚖️ Soft 제약 (선호)")
            for c in soft:
                weight = f" [가중치: {c.get('weight')}]" if c.get('weight') else ""
                lines.append(f"- **{c.get('name', '')}**: {c.get('description', '')}{weight}")
        lines.append("")

    # 메타데이터
    meta = model.get("metadata", {})
    if meta:
        lines.append("### 📊 모델 규모 추정")
        lines.append(f"- 추정 변수 수: **{meta.get('estimated_variable_count', '?')}개**")
        lines.append(f"- 추정 제약 수: **{meta.get('estimated_constraint_count', '?')}개**")
        lines.append(f"- 변수 타입: {', '.join(meta.get('variable_types_used', []))}")
        assumptions = meta.get("assumptions", [])
        if assumptions:
            lines.append(f"- 가정 사항:")
            for a in assumptions:
                lines.append(f"  - {a}")

    return "\n".join(lines)