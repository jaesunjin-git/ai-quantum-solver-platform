# ============================================================
# engine/model_generator.py — v1.0
# ============================================================
# 역할: LLM에게 데이터 요약과 분석 리포트를 보내고,
#       범용 수학 모델 JSON(Intermediate Representation)을 생성받는다.
#       이 JSON은 솔버에 독립적이며, OR-Tools/D-Wave 변환기의 입력이 된다.
# ============================================================

from __future__ import annotations

import json
import os
import logging
import asyncio
from typing import List,  Dict, Any, Optional

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

# ── 도메인 YAML 로더 ──
def _load_domain_yaml(domain: str) -> dict:
    """knowledge/domains/{domain}.yaml 또는 하위폴더에서 로드"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    domain_dir = os.path.join(base, "knowledge", "domains")
    if not os.path.isdir(domain_dir):
        return {}
    import yaml as _yaml
    # 1) 직하 파일 탐색
    for fname in os.listdir(domain_dir):
        if fname.endswith(".yaml"):
            fpath = os.path.join(domain_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                if data.get("domain") == domain:
                    logger.info(f"Domain YAML loaded: {fpath}")
                    return data
            except Exception:
                pass
    # 2) 하위폴더 탐색 (knowledge/domains/railway/constraints.yaml 등)
    sub_dir = os.path.join(domain_dir, domain)
    if os.path.isdir(sub_dir):
        merged = {}
        loaded_files = []
        _skip_keys = {'constraints', 'variables', 'sets', 'objectives', 'category_rules'}
        for fname in sorted(os.listdir(sub_dir)):
            if fname.endswith(".yaml"):
                fpath = os.path.join(sub_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = _yaml.safe_load(f) or {}
                    if data.get("domain") == domain or data.get("constraint_templates"):
                        for k, v in data.items():
                            if k in _skip_keys:
                                continue
                            if k not in merged:
                                merged[k] = v
                            elif isinstance(merged[k], dict) and isinstance(v, dict):
                                merged[k].update(v)
                        loaded_files.append(fname)
                        logger.info(f"Domain YAML merged: {fpath}")
                except Exception:
                    pass
        if merged:
            logger.info(f"Domain YAML loaded {loaded_files}, keys: {list(merged.keys())}")
            return merged
    return {}

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
    lines.append("예: 인원 N명 × 작업 M개 = binary 변수 N*M개")

    return "\n".join(lines)

# ============================================================
# 프롬프트 빌더
# ============================================================

def _build_data_guide(dataframes: dict) -> str:
    """DataBinder의 dataframes로부터 LLM용 데이터 소스 가이드 생성 (범용)"""
    guide_lines = []
    guide_lines.append("아래는 사용 가능한 정형 데이터 소스입니다. Set/Parameter 정의 시 반드시 이 목록의 source_file과 source_column만 사용하세요.")
    guide_lines.append("")
    
    # 비정형 블록 키 패턴 감지 (범용)
    block_patterns = []
    summary_keys = []
    for key in dataframes.keys():
        if "__summary" in key:
            summary_keys.append(key)
        parts = key.split("__")
        if len(parts) >= 2 and parts[-1] not in ("summary",):
            block_patterns.append(key)
    
    # ★ 정규화 데이터가 있으면 원본 파일은 data_guide에서 제외
    has_normalized = any(k.startswith("normalized/") for k in dataframes.keys())
    if has_normalized:
        # 정규화 파일의 원본 소스 파일명 수집
        _skip_originals = set()
        for _nk, _ndf in dataframes.items():
            if not _nk.startswith("normalized/"):
                continue
            # parameters.csv의 source 컬럼에서 원본 파일명 추출
            if "source" in _ndf.columns:
                for _src in _ndf["source"].dropna().unique():
                    # "text:파일명" 또는 "파일명:시트명" 또는 "파일명" 형식
                    _clean = str(_src).replace("text:", "").split(":")[0].strip()
                    if _clean:
                        _skip_originals.add(_clean)
        # 정규화 데이터가 존재하면 normalized/ 이외의 모든 원본 파일을 스킵
        # (trips.csv, parameters.csv에 모든 필요한 데이터가 이미 정리됨)
        for _ok in list(dataframes.keys()):
            if not _ok.startswith("normalized/"):
                _skip_originals.add(_ok)
        logger.info(f"Data guide: normalized mode, skipping {len(_skip_originals)} original keys")
    else:
        _skip_originals = set()

    for key in sorted(dataframes.keys()):
        # 개별 블록 테이블은 제외 (summary만 포함)
        if key in block_patterns:
            continue
        # 정규화 모드에서 원본 스킵
        if has_normalized and key in _skip_originals:
            continue
        df = dataframes[key]
        if len(df) == 0:
            continue
        
        # Unnamed 컬럼이 과반수인 시트는 비정형으로 표시
        unnamed_ratio = sum(1 for c in df.columns if "Unnamed" in str(c)) / max(len(df.columns), 1)
        if unnamed_ratio > 0.5:
            guide_lines.append(f"■ {key} ({len(df)}행) ← 비정형 데이터, 직접 참조 금지")
            guide_lines.append("")
            continue
        
        cols_info = []
        for col in df.columns[:15]:
            dtype = str(df[col].dtype)
            non_null = int(df[col].notna().sum())
            unique = int(df[col].nunique())
            sample = ""
            non_null_vals = df[col].dropna()
            if len(non_null_vals) > 0:
                sample = str(non_null_vals.iloc[0])[:30]
            cols_info.append(f"    - {col} ({dtype}, {non_null}행, {unique}고유값, 예: {sample})")
        
        marker = ""
        if key in summary_keys:
            marker = " ← 블록 파서 집계 데이터"
        
        guide_lines.append(f"■ {key} ({len(df)}행, {len(df.columns)}컬럼){marker}")
        guide_lines.extend(cols_info[:10])
        if len(cols_info) > 10:
            guide_lines.append(f"    ... 외 {len(cols_info)-10}개 컬럼")
        guide_lines.append("")
    
    # 권장 Set 매핑 (데이터 기반 자동 추론)
    guide_lines.append("★ 권장 Set 매핑:")
    
    # 운행/작업 set 후보: 가장 많은 행 + 5개 이상 컬럼 + Unnamed 없는 시트
    best_source = None
    best_rows = 0
    for key, df in dataframes.items():
        if key in block_patterns or "__summary" in key:
            continue
        unnamed_count = sum(1 for c in df.columns if "Unnamed" in str(c))
        if unnamed_count > 0:
            continue
        if len(df) > best_rows and len(df.columns) > 5:
            best_rows = len(df)
            first_col = str(df.columns[0])
            best_source = (key.split("::")[0] if "::" in key else key, first_col, len(df))
    
    if best_source:
        guide_lines.append(
            f"  - Set I (주요 작업/운행): source_file='{best_source[0]}', "
            f"source_column='{best_source[1]}' ({best_source[2]}개) ← 권장"
        )
    
    # 인원 수 추론: 숫자형 컬럼에서 "전체" 행의 값
    crew_size = None
    for key, df in dataframes.items():
        if key in block_patterns or "__summary" in key:
            continue
        for _, row in df.iterrows():
            row_str = " ".join(str(v) for v in row.values)
            if "전체" in row_str:
                for v in row.values:
                    try:
                        num = int(float(str(v)))
                        if 10 < num < 10000 and crew_size is None:
                            crew_size = num
                    except (ValueError, TypeError):
                        pass
    
    if crew_size:
        guide_lines.append(
            f"  - Set J (인원/자원): source_type='range', size={crew_size} "
            f"← 데이터에서 감지된 총 인원 수"
        )
    else:
        guide_lines.append(
            "  - Set J (인원/자원): 개별 목록이 없으면 source_type='range', size=N 사용"
        )
    
    if summary_keys:
        guide_lines.append(
            f"  - 집계 데이터는 {summary_keys[0]} 등 __summary 테이블에서 참조"
        )
    
    guide_lines.append("")
    guide_lines.append("★ 주의사항:")
    guide_lines.append("  - 'Unnamed: N' 형태의 컬럼은 비정형 데이터이므로 참조 금지")
    guide_lines.append("  - for_each와 sum.over에 동일 set을 사용하면 안 됩니다")
    guide_lines.append("    (예: for_each='j in J'이면 sum.over='i in I'로 다른 set 사용)")
    guide_lines.append("  - source_file/source_column이 없는 파라미터는 반드시 default_value를 설정하세요")
    guide_lines.append("")
    guide_lines.append("★ 데이터 컬럼 vs 파라미터 구분 (중요):")
    guide_lines.append("  - trips.csv의 컬럼(trip_dep_time, trip_arr_time, trip_duration 등)은 파라미터가 아닙니다")
    guide_lines.append("  - 이 값들은 sum의 coeff에서 source_column으로 참조하세요:")
    guide_lines.append("    예: \"coeff\": {\"param\": {\"name\": \"trip_duration\", \"source_column\": \"trip_duration\", \"source_file\": \"normalized/trips.csv\"}}")
    guide_lines.append("  - deadhead_time은 두 운행 사이 시간 차이로 계산되므로 별도 파라미터로 정의하지 마세요")
    guide_lines.append("  - parameters 배열에는 상수/규정값(max_work_minutes 등)만 넣으세요")
    
    return "\n".join(guide_lines)

def _build_modeling_prompt(
    csv_summary: str,
    analysis_report: str,
    domain: str,
    user_objective: Optional[str] = None,
    data_facts: Optional[dict] = None,
    data_guide: str = "",
    confirmed_problem=None,
) -> str:
    """LLM에게 수학 모델 JSON 생성을 요청하는 프롬프트를 조립 (YAML 기반 v2)"""
    import json as _json

    # YAML 프롬프트 설정 로드
    config = load_yaml_prompt("crew", "math_model")
    system = config.get("system", "")
    rules = config.get("rules", [])
    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

    soft_rules = config.get("soft_constraint_rules", [])
    soft_rules_text = "\n".join(f"  - {r}" for r in soft_rules)

    obj_rules = config.get("objective_rules", [])
    obj_rules_text = "\n".join(f"  - {r}" for r in obj_rules)

    constraint_schema_text = get_constraint_schema_text()
    model_schema = _get_model_schema()
    sections = config.get("sections", {})

    # 목적함수 지시
    if user_objective:
        objective_instruction = (
            "[사용자 지정 목적함수]\n"
            f"사용자가 다음 최적화 목표를 요청했습니다: \"{user_objective}\"\n"
            "이 목표를 objective 기본값으로 설정하고, 다른 목표는 alternatives에 넣으세요."
        )
    else:
        objective_instruction = (
            "[목적함수 추론]\n"
            "사용자가 명시적으로 목적함수를 지정하지 않았습니다.\n"
            "데이터와 도메인 특성을 분석하여 가장 적절한 목적함수를 추론하세요."
        )

    # ── confirmed_problem 섹션 조립 ──
    confirmed_section = ""
    if confirmed_problem:
        # 파라미터 목록
        _cp_params = confirmed_problem.get("parameters", {})
        _param_lines = []
        for _pid, _pval in _cp_params.items():
            _v = _pval.get("default", _pval.get("value", "")) if isinstance(_pval, dict) else _pval
            _param_lines.append(f"  - {_pid} (value: {_v})")
        _param_list_text = "\n".join(_param_lines) if _param_lines else "  (none)"
        _param_ids = ", ".join(_cp_params.keys())

        # 도메인 YAML에서 constraint_templates 로드
        _domain_yaml = _load_domain_yaml(domain)
        _ct = _domain_yaml.get("constraint_templates", {})

        _template_json_lines = []
        _required_vars = []
        _obj_template = None

        for _tid, _tdata in _ct.items():
            if _tid == "_note" or not isinstance(_tdata, dict):
                continue
            if _tdata.get("_type") == "objective_template":
                _obj_template = _tdata
                continue
            _clean = {k: v for k, v in _tdata.items() if k not in ("_note", "requires_variables")}
            _template_json_lines.append(
                f"  // {_tdata.get('description', _tid)}\n"
                f"  {_json.dumps(_clean, ensure_ascii=False)}"
            )
            for _rv in _tdata.get("requires_variables", []):
                if _rv not in _required_vars:
                    _required_vars.append(_rv)

        _templates_text = (
            "아래 JSON 제약 구조를 constraints 배열에 그대로 넣으세요.\n"
            "이름, 변수명, 파라미터명을 임의로 바꾸지 마세요.\n\n"
            + "\n\n".join(_template_json_lines)
        ) if _template_json_lines else "(없음)"

        _req_vars_text = "\n".join(f"  - {rv}" for rv in _required_vars) if _required_vars else ""

        # 목적함수 텍스트
        _obj_text = "  (추론 필요)"
        if _obj_template:
            _obj_text = (
                f"  방향: {_obj_template.get('type', 'minimize')}\n"
                f"  대상: {_obj_template.get('description', '')}\n"
                f"  expression: {_json.dumps(_obj_template.get('expression', {}), ensure_ascii=False)}"
            )
            _alts = _obj_template.get("alternatives", [])
            if _alts:
                _alt_texts = [a.get("description", "") for a in _alts if isinstance(a, dict)]
                if _alt_texts:
                    _obj_text += f"\n  대안: {', '.join(_alt_texts)}"
        elif confirmed_problem.get("objective"):
            _obj_info = confirmed_problem["objective"]
            _obj_text = f"  방향: {_obj_info.get('type', 'minimize')}\n  대상: {_obj_info.get('description', _obj_info.get('target', ''))}"

        # 데이터 컬럼
        _data_cols = _domain_yaml.get("data_columns", {})
        _dc_lines = []
        if _data_cols:
            for _src_key, _src_info in _data_cols.items():
                if not isinstance(_src_info, dict):
                    continue
                _sf = _src_info.get("source_file", "")
                _cols = _src_info.get("columns", {})
                for _cn, _cd in _cols.items():
                    _dc_lines.append(f"  - {_cn}: {_cd} (source_file: {_sf})")

        # ★ 하드 제약 목록
        _hard_constraints = confirmed_problem.get("hard_constraints", {})
        _hard_lines = []
        for _hid, _hdata in _hard_constraints.items():
            if isinstance(_hdata, dict):
                _desc = _hdata.get("name_ko", _hdata.get("description", _hid))
                _type = _hdata.get("type", "")
                _hard_lines.append(f"  - {_hid}: {_desc} [type={_type}]")
            else:
                _hard_lines.append(f"  - {_hid}")
        _hard_list_text = "\n".join(_hard_lines) if _hard_lines else "  (없음)"

        # ★ 소프트 제약 목록
        _soft_constraints = confirmed_problem.get("soft_constraints", {})
        _soft_lines = []
        for _sid, _sdata in _soft_constraints.items():
            if isinstance(_sdata, dict):
                _desc = _sdata.get("name_ko", _sdata.get("description", _sid))
                _weight = _sdata.get("weight", 1.0)
                _type = _sdata.get("type", "")
                _soft_lines.append(f"  - {_sid}: {_desc} [weight={_weight}, type={_type}]")
            else:
                _soft_lines.append(f"  - {_sid}")
        _soft_list_text = "\n".join(_soft_lines) if _soft_lines else "  (없음)"

        _hard_count = len(_hard_constraints)
        _soft_count = len(_soft_constraints)
        _total_count = _hard_count + _soft_count

        # 섹션별 텍스트 조립
        _parts = []
        _parts.append(sections.get("confirmed_problem", "").format(
            stage=confirmed_problem.get("stage", ""),
            variant=confirmed_problem.get("variant", ""),
        ))
        _parts.append(sections.get("objective", "").format(objective_text=_obj_text))
        _parts.append(sections.get("hard_constraints", "").format(
            hard_count=_hard_count, hard_constraint_list=_hard_list_text
        ))
        _parts.append(sections.get("soft_constraints", "").format(
            soft_count=_soft_count, soft_constraint_list=_soft_list_text
        ))
        if _templates_text:
            _parts.append(sections.get("constraint_templates", "").format(templates_text=_templates_text))
        if _req_vars_text:
            _parts.append(sections.get("required_variables", "").format(required_vars_text=_req_vars_text))
        if _dc_lines:
            _parts.append(sections.get("data_columns", "").format(
                data_columns_text="\n".join(_dc_lines)
            ))
        _parts.append(sections.get("parameters", "").format(
            param_count=len(_cp_params),
            param_list_text=_param_list_text,
            param_ids=_param_ids,
        ))

        confirmed_section = "\n".join(_parts)

        # 목적함수 지시 업데이트
        if _obj_template or confirmed_problem.get("objective"):
            objective_instruction = f"[확정된 목적함수 — 아래를 따르세요]\n{_obj_text}"

    # ── 최종 프롬프트 조립 (YAML template 사용) ──
    template = config.get("template", "")
    checklist = config.get("checklist", "")
    if confirmed_problem:
        _hard_count = len(confirmed_problem.get("hard_constraints", {}))
        _soft_count = len(confirmed_problem.get("soft_constraints", {}))
        _total_count = _hard_count + _soft_count
        checklist = checklist.format(
            hard_count=_hard_count, soft_count=_soft_count, total_count=_total_count
        )

    prompt = template.format(
        system=system,
        rules_text=rules_text,
        soft_rules_text=soft_rules_text,
        objective_rules_text=obj_rules_text,
        model_schema=model_schema,
        constraint_schema=constraint_schema_text,
        objective_instruction=objective_instruction,
        data_facts=_format_facts_for_model(data_facts),
        confirmed_section=confirmed_section,
        data_guide=data_guide,
        csv_summary=csv_summary[:4000],
        analysis_report=analysis_report[:3000],
        domain=domain,
    )

    if checklist:
        prompt += "\n\n" + checklist


    # === DEBUG: 프롬프트 저장 ===
    import os as _dbg_os
    _dbg_dir = _dbg_os.path.join('uploads', '94')
    _dbg_os.makedirs(_dbg_dir, exist_ok=True)
    with open(_dbg_os.path.join(_dbg_dir, 'debug_prompt.txt'), 'w', encoding='utf-8') as _dbg_f:
        _dbg_f.write(prompt)
    logger.info(f"DEBUG: prompt saved to uploads/94/debug_prompt.txt ({len(prompt)} chars)")
    # === END DEBUG ===

    return prompt

# 유틸
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
    retry_feedback: str = "",
    dataframes: Optional[Dict] = None,
    confirmed_problem: Optional[dict] = None,
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
        # data_guide 생성
        data_guide = ""
        if dataframes:
            data_guide = _build_data_guide(dataframes)
            logger.info(f"Data guide generated: {len(data_guide)} chars")

        prompt = _build_modeling_prompt(
            csv_summary=csv_summary,
            analysis_report=analysis_report,
            domain=domain,
            user_objective=user_objective,
            data_facts=data_facts,
            data_guide=data_guide, confirmed_problem=confirmed_problem,
        )

        # ★ 재시도 피드백이 있으면 프롬프트에 추가
        if retry_feedback:
            prompt += (
                "\n\n---\n## 이전 모델 검증 결과 (재생성 요청)\n"
                "아래 문제를 수정하여 모델을 다시 생성하세요:\n\n"
                f"{retry_feedback}\n"
            )
            logger.info(f"Retry feedback added to prompt ({len(retry_feedback)} chars)")

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

        # ★ 1계층 파라미터 주입 (동적, 하드코딩 없음)
        if confirmed_problem and model:
            _cp_params = confirmed_problem.get("parameters", {})
            _cp_ids_lower = {k.lower() for k in _cp_params.keys()}
            
            # LLM 생성 파라미터에서 1계층과 정확/유사 일치 제거
            _model_params = model.get("parameters", [])
            # 1계층 id에서 핵심 단어 추출 (단위어/접속어 제거)
            _stop = {"minutes", "min", "time", "per", "param"}
            _filter = {"minutes", "min", "time", "per", "param", "duty", "crew", "trip"}
            _cp_keywords = {}
            for _cid in _cp_params.keys():
                _words = set(_cid.lower().split("_")) - _stop
                if _words:
                    _cp_keywords[_cid] = _words
            _filtered = []
            _removed = []
            for _mp in _model_params:
                _mid = (_mp.get("id") or "").lower()
                # 정확 일치
                if _mid in _cp_ids_lower:
                    _removed.append(_mp.get("id", ""))
                    continue
                # 유사 일치: LLM 파라미터의 핵심 단어가 1계층 키워드를 모두 포함
                _mid_words = set(_mid.split("_")) - _filter
                _is_similar = False
                for _cid, _cwords in _cp_keywords.items():
                    if _cwords and _cwords.issubset(_mid_words):
                        _removed.append(f"{_mp.get('id', '')} (similar to {_cid})")
                        _is_similar = True
                        break
                if _is_similar:
                    continue
                _filtered.append(_mp)

            
            # 1계층 파라미터 주입
            for _pid, _pval in _cp_params.items():
                if isinstance(_pval, dict):
                    _dv = _pval.get("default", _pval.get("value"))
                else:
                    _dv = _pval
                try:
                    _dv = float(_dv) if _dv is not None else None
                except (ValueError, TypeError):
                    pass
                _filtered.append({
                    "id": _pid,
                    "name": _pid,
                    "type": "scalar",
                    "default_value": _dv,
                    "source_file": "normalized/parameters.csv",
                    "source_column": "value",
                    "auto_injected": True,
                    "layer": 1,
                })
            
            model["parameters"] = _filtered

            # ★ 제거된 파라미터의 id → 1계층 id 매핑으로 제약조건 내 참조 치환
            _rename_map = {}
            for _r in _removed:
                # "old_id (similar to layer1_id)" 형태에서 추출
                if "(similar to " in str(_r):
                    _old = str(_r).split(" (similar to ")[0]
                    _new = str(_r).split("(similar to ")[1].rstrip(")")
                    _rename_map[_old] = _new
                else:
                    # 정확 일치는 이름이 같으므로 치환 불필요
                    pass
            
            if _rename_map:
                # 제약조건 내 param name 치환 (재귀적)
                def _rename_params(node, rmap):
                    if isinstance(node, dict):
                        # param 노드의 name 치환
                        if "param" in node and isinstance(node["param"], dict):
                            pname = node["param"].get("name", "")
                            if pname in rmap:
                                node["param"]["name"] = rmap[pname]
                        if "name" in node and node.get("name") in rmap:
                            # var 노드가 아닌 경우만
                            if "var" not in str(type(node)):
                                pass
                        # 모든 하위 노드 순회
                        for k, v in node.items():
                            _rename_params(v, rmap)
                    elif isinstance(node, list):
                        for item in node:
                            _rename_params(item, rmap)
                
                for _con in model.get("constraints", []):
                    _rename_params(_con, _rename_map)
                    # expression 문자열도 치환
                    if "expression" in _con and isinstance(_con["expression"], str):
                        for _old_id, _new_id in _rename_map.items():
                            _con["expression"] = _con["expression"].replace(_old_id, _new_id)
                
                # objective 내 참조도 치환
                _obj = model.get("objective")
                if _obj:
                    _rename_params(_obj, _rename_map)
                    if "expression" in _obj and isinstance(_obj["expression"], str):
                        for _old_id, _new_id in _rename_map.items():
                            _obj["expression"] = _obj["expression"].replace(_old_id, _new_id)
                
                logger.info(f"Param rename map applied: {_rename_map}")

            logger.info(
                f"Layer-1 inject: removed {len(_removed)} duplicates "
                f"({_removed[:5]}), injected {len(_cp_params)} from confirmed_problem"
            )

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
# 제약 수정 함수 (에러 제약만 재생성)
# ============================================================
async def repair_constraints(
    model: Dict[str, Any],
    error_constraints: List[Dict[str, Any]],
    valid_constraint_names: List[str],
) -> Dict[str, Any]:
    """
    에러가 있는 제약만 LLM에게 수정 요청.
    전체 모델 컨텍스트(변수, 파라미터, 집합, 올바른 제약)를 제공하여
    의존성을 유지하면서 수정.

    Returns:
        {
            "success": bool,
            "added_variables": [...],
            "replaced_constraints": [...],
            "added_constraints": [...],
            "removed_constraints": [...],
            "error": str or None
        }
    """
    if not _model:
        return {"success": False, "error": "LLM 모델이 초기화되지 않았습니다."}

    try:
        from utils.prompt_loader import load_yaml_prompt, get_constraint_schema_text

        config = load_yaml_prompt("crew", "constraint_repair")
        system = config.get("system", "")
        rules = config.get("rules", [])
        rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

        # 출력 스키마
        output_schema = json.dumps({
            "added_variables": [{"id": "str", "name": "str", "type": "binary|integer|continuous", "indices": ["SET_ID"], "description": "str"}],
            "replaced_constraints": [{"name": "str", "description": "str", "for_each": "str", "lhs": {}, "operator": "str", "rhs": {}, "priority": "hard|soft", "expression": "str"}],
            "added_constraints": [{"name": "str", "description": "str", "for_each": "str", "lhs": {}, "operator": "str", "rhs": {}, "priority": "hard|soft", "expression": "str"}],
            "removed_constraints": ["constraint_name"]
        }, ensure_ascii=False, indent=2)

        constraint_schema = get_constraint_schema_text()

        # 변수 목록 텍스트
        variables_text = ""
        for v in model.get("variables", []):
            vid = v.get("id", "?")
            vtype = v.get("type", "?")
            indices = v.get("indices", [])
            desc = v.get("description", "")
            variables_text += f"- {vid} (type={vtype}, indices={indices}): {desc}\n"

        # 파라미터 목록 텍스트
        parameters_text = ""
        for p in model.get("parameters", []):
            pid = p.get("id", p.get("name", "?"))
            ptype = p.get("type", "?")
            src = p.get("source_file", "")
            col = p.get("source_column", "")
            dv = p.get("default_value", "없음")
            parameters_text += f"- {pid} (type={ptype}, source={src}::{col}, default={dv})\n"

        # 집합 목록 텍스트
        sets_text = ""
        for s in model.get("sets", []):
            sid = s.get("id", "?")
            sname = s.get("name", "?")
            src_type = s.get("source_type", "column")
            size = s.get("size", "?")
            sets_text += f"- {sid} ({sname}): source_type={src_type}, size={size}\n"

        # 올바른 제약 텍스트
        valid_constraints_text = ""
        for c in model.get("constraints", []):
            if c.get("name") in valid_constraint_names:
                valid_constraints_text += f"- {c.get('name')}: {c.get('description', '')}\n"
                valid_constraints_text += f"  expression: {c.get('expression', 'N/A')}\n"

        # 에러 제약 텍스트
        error_constraints_text = ""
        for ec in error_constraints:
            cname = ec.get("name", "?")
            reason = ec.get("error_reason", "불명")
            desc = ec.get("description", "")
            expr = ec.get("expression", "N/A")
            error_constraints_text += f"- {cname}: {desc}\n"
            error_constraints_text += f"  현재 expression: {expr}\n"
            error_constraints_text += f"  에러 사유: {reason}\n\n"

        # 프롬프트 조립
        prompt = f"""{system}

중요 규칙:
{rules_text}

출력 JSON 스키마:
{output_schema}

제약조건 작성 형식:
{constraint_schema}

=== 현재 모델 컨텍스트 ===

[변수 목록]
{variables_text}

[파라미터 목록]
{parameters_text}

[집합 목록]
{sets_text}

[올바른 제약 (수정 금지)]
{valid_constraints_text}

=== 수정 대상 ===

[에러 제약과 에러 사유]
{error_constraints_text}

위 에러 제약을 수정하여 JSON으로 출력하세요."""

        logger.info(f"Repair prompt length: {len(prompt)} chars, error constraints: {len(error_constraints)}")

        response = await asyncio.to_thread(
            _model.generate_content, prompt
        )

        raw_text = response.text.strip()
        logger.info(f"Repair response length: {len(raw_text)}")

        # JSON 파싱
        result = _parse_model_json(raw_text)
        if not result:
            return {"success": False, "error": "수정 응답에서 유효한 JSON을 추출할 수 없습니다."}

        return {
            "success": True,
            "added_variables": result.get("added_variables", []),
            "replaced_constraints": result.get("replaced_constraints", []),
            "added_constraints": result.get("added_constraints", []),
            "removed_constraints": result.get("removed_constraints", []),
            "error": None,
        }

    except Exception as e:
        logger.error(f"Constraint repair failed: {e}", exc_info=True)
        return {"success": False, "error": f"제약 수정 중 오류: {str(e)}"}


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