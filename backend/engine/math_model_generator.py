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
MATH_MODEL_SCHEMA = """
{
  "model_version": "1.0",
  "problem_name": "<문제 이름>",
  "domain": "<도메인 키: railway, aviation, bus, logistics, hospital, generic>",

  "sets": [
    {
      "id": "<집합 ID (영문 대문자, 예: I, J, K)>",
      "name": "<집합 이름 (한국어)>",
      "description": "<설명>",
      "source_column": "<데이터에서 이 집합을 구성하는 컬럼명>",
      "source_file": "<해당 컬럼이 있는 파일명>"
    }
  ],

  "parameters": [
    {
      "id": "<파라미터 ID (영문 소문자)>",
      "name": "<파라미터 이름 (한국어)>",
      "type": "<numeric | boolean | constant | categorical>",
      "source_column": "<데이터 컬럼명 (파일에서 읽는 경우)>",
      "source_file": "<파일명>",
      "value": "<고정 상수인 경우 값>",
      "unit": "<단위 (분, 시간, 원, km 등)>"
    }
  ],

  "variables": [
    {
      "id": "<변수 ID (영문 소문자, 예: x, y, z)>",
      "name": "<변수 이름 (한국어)>",
      "type": "<binary | integer | continuous>",  // 반드시 이 3가지 중 하나만 사용. categorical 사용 금지
      "indices": ["<집합 ID>", "<집합 ID>"],
      "description": "<변수의 의미>",
      "lower_bound": null,
      "upper_bound": null
    }
  ],

  "objective": {
    "type": "<minimize | maximize>",
    "description": "<목적함수 설명 (한국어)>",
    "expression": "<수식 표현 (Python-like pseudo code)>",
    "alternatives": [
      {
        "type": "<minimize | maximize>",
        "description": "<대안 목적함수 설명>",
        "expression": "<수식>"
      }
    ]
  },

  "constraints": [
    {
      "id": "<제약 ID (c1, c2, ...)>",
      "name": "<제약 이름 (한국어)>",
      "category": "<hard | soft>",
      "description": "<제약 설명>",
      "expression": "<수식 표현 (Python-like pseudo code)>",
      "for_each": "<반복 범위 (예: 'i in I', 'j in J')>",
      "weight": null
    }
  ],

  "metadata": {
    "estimated_variable_count": "<변수 총 개수 추정 (정수)>",
    "estimated_constraint_count": "<제약 총 개수 추정 (정수)>",
    "variable_types_used": ["<사용된 변수 타입 목록>"],
    "data_files_required": ["<필요한 파일명 목록>"],
    "assumptions": ["<모델링 시 가정한 사항 목록>"]
  }
}
"""

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
    """LLM에게 수학 모델 JSON 생성을 요청하는 프롬프트를 조립"""

    objective_instruction = ""
    if user_objective:
        objective_instruction = f"""
[사용자 지정 목적함수]
사용자가 다음과 같은 최적화 목표를 요청했습니다:
"{user_objective}"
이 목표를 objective의 기본값으로 설정하고, 다른 가능한 목표는 alternatives에 넣으세요.
"""
    else:
        objective_instruction = """
[목적함수 추론]
사용자가 명시적으로 목적함수를 지정하지 않았습니다.
데이터와 도메인 특성을 분석하여 가장 적절한 목적함수를 추론하세요.
다른 가능한 목적함수는 alternatives에 넣으세요.
"""

    prompt = f"""당신은 최적화 문제의 수학적 모델링 전문가입니다.

중요 규칙:
- variables의 type은 반드시 binary, integer, continuous 중 하나만 사용하세요.
- parameters의 type은 numeric, boolean, constant, categorical 중 하나를 사용하세요.
- variables의 index는 반드시 sets에 정의된 id만 사용하세요.
아래의 데이터 요약과 분석 리포트를 바탕으로, 주어진 JSON 스키마에 맞는 수학 모델을 생성하세요.

[핵심 규칙]
1. 반드시 아래 JSON 스키마 구조를 정확히 따르세요.
2. sets의 source_column은 실제 데이터에 존재하는 컬럼명을 사용하세요.
3. parameters의 source_column도 실제 데이터 컬럼명과 정확히 일치해야 합니다.
4. expression은 Python 스타일의 의사코드로 작성하세요.
5. 데이터에 없는 정보를 추측하지 마세요. 확실하지 않은 경우 assumptions에 기록하세요.
6. hard 제약은 반드시 지켜야 하는 것, soft 제약은 가능하면 지키되 위반 시 페널티입니다.
7. metadata의 estimated_variable_count는 sets의 크기를 곱하여 계산하세요.
8. JSON만 출력하세요. 설명이나 마크다운 코드블록 없이 순수 JSON만 반환하세요.
9. expression은 짧고 간결하게 작성하세요. 예: "sum(x[i,j] for j in J) == 1" 형태.
   긴 자연어 설명 대신 수학적 표기를 사용하세요.
10. constraint의 expression에 부등호(<, >, <=, >=)를 사용할 때는 문자열 안에서 그대로 쓰세요.
    HTML 엔티티(&lt; 등)를 사용하지 마세요.
11. null 값을 사용하지 마세요. 값이 없으면 빈 문자열("")이나 0을 사용하세요.
12. weight 필드: hard 제약은 weight를 생략하거나 0으로, soft 제약은 1-10 사이 정수로 설정하세요.

{objective_instruction}

[검증된 데이터 팩트 - 코드로 계산된 확정값]
{_format_facts_for_model(data_facts)}

[데이터 요약]
{csv_summary[:4000]}

[분석 리포트]
{analysis_report[:3000]}

[도메인]
{domain}

[JSON 스키마]
{MATH_MODEL_SCHEMA}

위 스키마의 모든 필드를 채워서 순수 JSON으로 반환하세요.
"""
    return prompt


# ============================================================
# JSON 파싱 유틸
# ============================================================
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
        r':\s*(\d+\s*[\+\-\*\/]\s*\d+(?:\s*[\+\-\*\/]\s*\d+)*)',
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

    # 3차 시도: 줄 단위로 문제 줄 제거
    lines = fixed.split("\n")
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

    # 제약 카테고리 확인
    valid_categories = {"hard", "soft"}
    for con in model.get("constraints", []):
        if con.get("category") not in valid_categories:
            warnings.append(f"제약 '{con.get('id')}'의 category가 유효하지 않습니다: {con.get('category')}")

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
        hard = [c for c in constraints if c.get("category") == "hard"]
        soft = [c for c in constraints if c.get("category") == "soft"]

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