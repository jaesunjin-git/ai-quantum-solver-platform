"""
engine/gates/gate2_model_validate.py
────────────────────────────────────
Gate 2: 수학 모델 유효성 검증

LLM이 생성한 수학 모델 JSON을 실제 데이터와 대조하여 검증한다.
LLM 호출 없이 규칙 기반으로 동작한다.

검증 항목:
  1. Set 바인딩 — source_file/source_column이 실제 데이터에 존재하는가
  2. Parameter 바인딩 — 모델이 참조하는 파라미터가 데이터에 매핑 가능한가
  3. 제약 구조 — operator가 비교 연산자인가, 변수/파라미터가 정의되어 있는가
  4. 변수 수 재계산 — 실제 set 크기 기반으로 정확한 변수 수 산출
"""

import logging
from utils.prompt_loader import build_prompt_from_yaml
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VALID_OPERATORS = {"==", "<=", ">=", "<", ">", "!="}


# ──────────────────────────────────────────────
# Alias Map: constraints.yaml hints → param IDs
# ──────────────────────────────────────────────
def _build_param_alias_map():
    """constraints.yaml의 detection_hints.ko → parameter(s) 매핑 생성.
    Returns: dict mapping Korean hint keywords to English param IDs.
    Example: {"최대승무시간": "max_driving_minutes", "준비시간": "prep_time_minutes"}
    """
    import os
    alias_map = {}
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # Try folder-based domain first
        for domain_name in os.listdir(os.path.join(base, "knowledge", "domains")):
            cpath = os.path.join(base, "knowledge", "domains", domain_name, "constraints.yaml")
            if not os.path.isfile(cpath):
                continue
            with open(cpath, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
            # constraints.yaml 구조: 최상위 "constraints" 섹션에 모든 제약,
            # 각 제약 내부에 category: hard/soft 필드로 구분
            for cid, cdef in (cdata.get("constraints") or {}).items():
                    if not isinstance(cdef, dict):
                        continue
                    _raw_hints = cdef.get("detection_hints") or {}
                    if isinstance(_raw_hints, list):
                        hints_ko = _raw_hints
                    elif isinstance(_raw_hints, dict):
                        hints_ko = _raw_hints.get("ko", [])
                    else:
                        hints_ko = []
                    # single_param
                    param_id = cdef.get("parameter")
                    if param_id and hints_ko:
                        for hint in hints_ko:
                            alias_map[hint.strip()] = param_id
                        # Also generate concatenated hint keys
                        if len(hints_ko) >= 2:
                            alias_map["".join(h.strip() for h in hints_ko)] = param_id
                    # compound params
                    params_raw = cdef.get("parameters") or {}
                    # list인 경우 dict로 변환
                    if isinstance(params_raw, list):
                        params_dict = {p: {} for p in params_raw if isinstance(p, str)}
                    else:
                        params_dict = params_raw
                    if params_dict and hints_ko:
                        for pid in params_dict:
                            # Map each sub-param by its name parts
                            alias_map[pid] = pid
                            # Also try Korean name parts from the name_ko
                            name_ko = cdef.get("name_ko", "")
                            if name_ko:
                                alias_map[name_ko] = pid
        logger.info(f"Param alias map built: {len(alias_map)} entries")
    except Exception as e:
        logger.warning(f"Failed to build param alias map: {e}")
    return alias_map





# ──────────────────────────────────────────────
# 범용 파라미터 자동 바인딩 (도메인 무관)
# ──────────────────────────────────────────────

def _parse_value_string(s):
    """문자열에서 숫자 값 추출 (범용). 예: '40분'->40, '3시간'->180, '225:33'->225.55"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None

    import re

    # 시간:분 형태 (예: 225:33, 157:58)
    m = re.match(r"^(\d+):(\d+)$", s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60, 2)

    # N시간 (예: 3시간 -> 180분)
    m = re.match(r"^(\d+\.?\d*)\s*시간$", s)
    if m:
        return round(float(m.group(1)) * 60, 2)

    # N분 (예: 40분 -> 40)
    m = re.match(r"^(\d+\.?\d*)\s*분$", s)
    if m:
        return float(m.group(1))

    # N회, N개사업 등 (숫자만 추출)
    m = re.match(r"^(\d+\.?\d*)\s*[회개명건]", s)
    if m:
        return float(m.group(1))

    # 순수 숫자
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _tokenize_korean(text):
    """한국어 텍스트를 키워드 토큰으로 분리 (범용)"""
    import re
    text = str(text).lower().strip()
    text = re.sub(r"[_\-\s]+", " ", text)
    # 한국어: 조사/어미 간단 제거
    tokens = re.split(r"[\s_\-/()（）\[\]]+", text)
    tokens = [t for t in tokens if len(t) > 0]
    return set(tokens)


def _token_similarity(a, b):
    """두 텍스트의 토큰 유사도 (Jaccard)"""
    ta = _tokenize_korean(a)
    tb = _tokenize_korean(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union) if union else 0.0


def _extract_kv_entries(dataframes):
    """모든 dataframe에서 key-value 쌍 추출 (범용).
    2~3열, <30행인 시트에서 첫 열=키, 나머지=값으로 취급.
    반환: [(sheet_key, key_text, value_text, parsed_number), ...]
    """
    entries = []
    if not dataframes:
        return entries

    for sheet_key, df in dataframes.items():
        # DIA 블록 데이터 제외
        if "__DIA " in sheet_key or "__Block " in sheet_key:
            continue
        # key-value 시트 조건: 열 2~3개, 행 30개 미만
        if not (2 <= len(df.columns) <= 3 and len(df) < 30):
            continue

        for _, row in df.iterrows():
            key_val = str(row.iloc[0]).strip() if row.iloc[0] is not None else ""
            if not key_val or key_val == "nan":
                continue

            # 2열 시트: 값은 두 번째 열
            if len(df.columns) == 2:
                raw_val = row.iloc[1]
                parsed = _parse_value_string(raw_val)
                entries.append((sheet_key, key_val, str(raw_val), parsed))
            # 3열 시트: 세부+시간 등 → 키를 "항목_세부"로 조합
                # 값은 세 번째 열
            elif len(df.columns) == 3:
                detail = str(row.iloc[1]).strip() if row.iloc[1] is not None else ""
                combined_key = f"{key_val} {detail}".strip()
                raw_val = row.iloc[2]
                parsed = _parse_value_string(raw_val)
                entries.append((sheet_key, combined_key, str(raw_val), parsed))
                # 항목만으로도 매칭 가능하게
                entries.append((sheet_key, key_val, str(raw_val), parsed))

    return entries


def _extract_summary_stats(dataframes):
    """__summary 테이블에서 통계값 추출 (범용).
    반환: [(sheet_key, col_name, stat_type, value), ...]
    """
    stats = []
    if not dataframes:
        return stats

    for sheet_key, df in dataframes.items():
        if "__summary" not in sheet_key:
            continue
        for col in df.columns:
            if df[col].dtype in ("int64", "float64"):
                vals = df[col].dropna()
                if len(vals) == 0:
                    continue
                stats.append((sheet_key, col, "min", float(vals.min())))
                stats.append((sheet_key, col, "max", float(vals.max())))
                stats.append((sheet_key, col, "mean", round(float(vals.mean()), 2)))
    return stats



def _build_bind_prompt(unbound_params, kv_entries, summary_stats):
    """프롬프트 파일(parameter_bind.yaml)에서 템플릿을 로드하여 바인딩 프롬프트 생성"""
    from utils.prompt_loader import load_yaml_prompt

    config = load_yaml_prompt("crew", "parameter_bind")

    # system, rules, schema 조립
    system = config.get("system", "")
    rules = config.get("rules", [])
    rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))
    schema = config.get("schema", "")

    # 데이터 변수 준비
    params_text = "\n".join(f"- {p}" for p in unbound_params)

    seen = set()
    kv_lines = []
    for sheet, key, raw, parsed in kv_entries:
        if parsed is None:
            continue
        entry_key = f"{key}={parsed}"
        if entry_key in seen:
            continue
        seen.add(entry_key)
        kv_lines.append(f"- [{sheet}] {key} = {raw} (숫자값: {parsed})")
    kv_text = "\n".join(kv_lines) if kv_lines else "(없음)"

    summary_lines = []
    for sheet, col, stype, val in summary_stats:
        summary_lines.append(f"- [{sheet}] {col}.{stype} = {val}")
    summary_text = "\n".join(summary_lines) if summary_lines else "(없음)"

    # 템플릿 조립 + 변수 치환
    template = config.get("template", "")
    if not template:
        template = "{system}\n\n{rules_text}\n\n{unbound_params}\n\n{kv_data}\n\n{summary_data}"

    prompt = template
    for key, value in {
        "system": system,
        "rules_text": rules_text,
        "schema": schema,
        "unbound_params": params_text,
        "kv_data": kv_text,
        "summary_data": summary_text,
    }.items():
        prompt = prompt.replace("{" + key + "}", str(value))

    return prompt

def auto_bind_unbound_parameters(model, dataframes):
    """
    범용 자동 바인딩 (LLM 기반, 동기):
    1) KV 엔트리 + Summary 통계 수집
    2) LLM에게 매칭 요청
    3) 매칭 결과 적용, 실패 시 user_input_required 마킹
    """
    import json as json_mod
    import re
    import logging
    logger = logging.getLogger(__name__)

    corrections = []
    still_unbound = []

    # unbound 파라미터 수집
    unbound_params = []
    for p in model.get("parameters", []):
        pid = p.get("id", p.get("name", ""))
        sf = p.get("source_file") or ""
        sc = p.get("source_column") or ""
        dv = p.get("default_value", p.get("default"))
        if not sf and not sc and dv is None:
            unbound_params.append(pid)

    if not unbound_params:
        return corrections, still_unbound

    kv_entries = _extract_kv_entries(dataframes)
    summary_stats = _extract_summary_stats(dataframes)

    try:
        import google.generativeai as genai
        from core.config import settings

        genai.configure(api_key=settings.GOOGLE_API_KEY)
        bind_model = genai.GenerativeModel(
            settings.MODEL_ANALYSIS,
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                max_output_tokens=4096,
            ),
        )

        prompt = _build_bind_prompt(unbound_params, kv_entries, summary_stats)
        logger.info(f"Auto-bind LLM prompt length: {len(prompt)}")

        response = bind_model.generate_content(prompt)
        raw = response.text.strip()
        logger.info(f"Auto-bind LLM response length: {len(raw)}")
        logger.debug(f"Auto-bind LLM response: {raw[:500]}")

        # JSON 파싱
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            result = json_mod.loads(json_match.group())
        else:
            logger.warning("Auto-bind: LLM 응답에서 JSON을 찾을 수 없음")
            result = {"bindings": []}

        # 결과 적용
        param_map = {p.get("id", p.get("name", "")): p for p in model.get("parameters", [])}
        bound_ids = set()

        for binding in result.get("bindings", []):
            pid = binding.get("parameter", "")
            value = binding.get("value")
            source = binding.get("source", "")
            reasoning = binding.get("reasoning", "")

            if pid in param_map and value is not None:
                param_map[pid]["default_value"] = value
                param_map[pid]["auto_bound"] = True
                param_map[pid]["auto_bound_source"] = source
                param_map[pid]["auto_bound_reasoning"] = reasoning
                corrections.append(
                    f"Parameter '{pid}' -> {value} "
                    f"(source: {source}, reason: {reasoning})"
                )
                bound_ids.add(pid)

        for pid in unbound_params:
            if pid not in bound_ids:
                if pid in param_map:
                    param_map[pid]["user_input_required"] = True
                still_unbound.append(pid)

    except Exception as e:
        logger.error(f"Auto-bind LLM 호출 실패: {e}", exc_info=True)
        param_map = {p.get("id", p.get("name", "")): p for p in model.get("parameters", [])}
        for pid in unbound_params:
            if pid in param_map:
                param_map[pid]["user_input_required"] = True
            still_unbound.append(pid)

    return corrections, still_unbound

def run(math_model: Dict,
        data_profile: Optional[Dict] = None,
        dataframes: Optional[Dict] = None,
         confirmed_problem: Optional[Dict] = None) -> Dict[str, Any]:
    """
    메인 검증 함수.

    Args:
        math_model: LLM이 생성한 수학 모델 JSON
        data_profile: Gate 1이 생성한 프로파일 (옵션)
        dataframes: DataBinder._dataframes (옵션, set 크기 계산용)

    Returns:
        {
            "valid": bool,
            "errors": [...],        # 치명적 오류 (모델 재생성 필요)
            "warnings": [...],      # 경고 (진행 가능하지만 주의)
            "corrections": {...},   # 자동 교정된 항목
            "actual_variable_count": int,
            "actual_set_sizes": {set_id: int},
        }
    """
    errors: List[str] = []
    warnings: List[str] = []
    corrections: Dict[str, Any] = {}
    set_sizes: Dict[str, int] = {}

    # ★ 데이터 컬럼 이름 수집 (param 미정의 경고 제외용)
    data_column_names = set()
    if dataframes:
        for _dk, _df in dataframes.items():
            for _col in _df.columns:
                data_column_names.add(str(_col).strip())

    sets = math_model.get("sets", [])
    variables = math_model.get("variables", [])
    constraints = math_model.get("constraints", [])
    parameters = math_model.get("parameters", [])
    metadata = math_model.get("metadata", {})

    # ── 1. Set 검증 ──
    # struct fix에서 자동 주입 가능한 set은 deferred check
    # 판단 기준: normalized 디렉토리에 해당 set의 데이터 파일이 존재
    import os as _os_gate2
    _injectable_sets = set()
    if _struct_project_id:
        _norm_dirs = [
            f"uploads/{_struct_project_id}/normalized",
            f"uploads/{_struct_project_id}/phase1",
        ]
        for s in sets:
            sid = s.get("id", "")
            source_type = s.get("source_type", "")
            # source가 explicit이거나 미정의인데, normalized에 파일 존재
            if source_type in ("explicit", "") or not s.get("source_file"):
                for _nd in _norm_dirs:
                    for ext in (".json", ".csv"):
                        if _os_gate2.path.exists(_os_gate2.path.join(_nd, f"{sid}{ext}")):
                            _injectable_sets.add(sid)
                            break

    _deferred_set_errors = []

    set_ids = set()
    for s in sets:
        sid = s.get("id", "")
        set_ids.add(sid)
        size = _validate_set(s, data_profile, dataframes)
        set_sizes[sid] = size

        if size == 0:
            if sid in _injectable_sets:
                # struct fix에서 주입될 수 있으므로 error 대신 deferred
                logger.info(f"Set '{sid}': deferred to struct fix (file found in normalized)")
                _deferred_set_errors.append(
                    f"Set '{sid}': 크기를 결정할 수 없음 "
                    f"(source_file={s.get('source_file')}, "
                    f"source_column={s.get('source_column')}, "
                    f"source_type={s.get('source_type', 'explicit')})"
                )
            else:
                errors.append(
                    f"Set '{sid}': 크기를 결정할 수 없음 "
                    f"(source_file={s.get('source_file')}, "
                    f"source_column={s.get('source_column')}, "
                    f"source_type={s.get('source_type')})"
                )
        elif size < 0:
            errors.append(f"Set '{sid}': 유효하지 않은 크기 ({size})")

    # ── 2. Variable 검증 ──
    var_ids = set()
    actual_var_count = 0
    for v in variables:
        vid = v.get("id", "")
        var_ids.add(vid)
        indices = v.get("indices", [])

        # 인덱스가 정의된 set을 참조하는지
        for idx in indices:
            if idx not in set_ids:
                errors.append(f"Variable '{vid}': 인덱스 '{idx}'가 정의된 set에 없음")

        # 변수 수 계산
        if not indices:
            actual_var_count += 1
        else:
            product = 1
            for idx in indices:
                product *= set_sizes.get(idx, 0)
            if product > 0:
                actual_var_count += product
            else:
                warnings.append(f"Variable '{vid}': set 크기 미확인으로 변수 수 계산 불가")

    # LLM 추정치와 비교
    llm_estimate = metadata.get("estimated_variable_count", 0)
    if llm_estimate > 0 and actual_var_count > 0:
        ratio = abs(actual_var_count - llm_estimate) / max(llm_estimate, 1)
        if ratio > 0.5:
            corrections["estimated_variable_count"] = {
                "old": llm_estimate,
                "new": actual_var_count,
                "reason": f"실제 set 크기 기반 재계산 (차이 {ratio:.0%})"
            }
            # 모델 내 metadata도 교정
            metadata["estimated_variable_count"] = actual_var_count
            warnings.append(
                f"변수 수 교정: LLM 추정 {llm_estimate} → 실제 {actual_var_count}"
            )

    # ── 3. Parameter 검증 ──
    param_names = {p.get("name", p.get("id", "")) for p in parameters}
    if data_profile:
        available_columns = set()
        for sheet_info in data_profile.get("files", {}).values():
            for col_name in sheet_info.get("columns", {}):
                available_columns.add(col_name)

        for p in parameters:
            pname = p.get("name", p.get("id", ""))
            source_file = p.get("source_file", "")
            source_column = p.get("source_column", "")

            if source_column and source_column not in available_columns:
                # 유사 이름 매칭 시도
                matched = _fuzzy_match_column(source_column, available_columns)
                if matched:
                    warnings.append(
                        f"Parameter '{pname}': '{source_column}' → '{matched}'로 유사 매칭"
                    )
                else:
                    warnings.append(
                        f"Parameter '{pname}': source_column '{source_column}'이 "
                        f"데이터에 없음 (바인딩 시 None 가능성)"
                    )

    # 비정형 시트를 source로 참조하는지 체크
    if data_profile:
        non_tabular = set(data_profile.get("summary", {}).get("non_tabular_sheets", []))
        for p in parameters:
            source_file = p.get("source_file", "")
            for nt_sheet in non_tabular:
                if source_file and source_file in nt_sheet:
                    warnings.append(
                        f"Parameter '{p.get('name', '')}': "
                        f"비정형 블록 시트 '{nt_sheet}'를 참조 — 파싱 오류 가능성"
                    )

    # ── 4. Constraint 검증 ──
    for c in constraints:
        cname = c.get("name", "unknown")
        op = c.get("operator", "")

        # operator 검증
        if op not in VALID_OPERATORS:
            errors.append(
                f"Constraint '{cname}': operator '{op}'는 비교 연산자가 아님 "
                f"(허용: {VALID_OPERATORS})"
            )

        # lhs/rhs에서 참조하는 변수/파라미터 검증
        lhs = c.get("lhs", {})
        rhs = c.get("rhs", {})
        for side_name, side in [("lhs", lhs), ("rhs", rhs)]:
            _check_node_refs(side, cname, side_name, var_ids, param_names, warnings, corrections, data_column_names)

        # for_each에서 참조하는 set 검증
        for_each = c.get("for_each", "")
        if for_each:
            referenced_sets = re.findall(r"in\s+(\w+)", for_each)
            for rs in referenced_sets:
                if rs not in set_ids:
                    errors.append(
                        f"Constraint '{cname}': for_each에서 '{rs}' set을 참조하지만 정의되지 않음"
                    )

    # ── 5. 결과 요약 ──

    # ── 추가 검증: 제약 구조 심층 분석 ──
    set_ids = {s.get("id", s.get("name", "")): s for s in sets}
    
    for con in constraints:
        cname = con.get("name", "unknown")
        for_each = con.get("for_each", "") or ""
        lhs = con.get("lhs", {}) or {}
        
        # 검증1: for_each와 sum.over가 동일 인덱스 → 의미 없는 이중 루프
        if for_each and isinstance(lhs, dict) and "sum" in lhs:
            sum_node = lhs.get("sum", {})
            if not isinstance(sum_node, dict):
                continue
            sum_over = sum_node.get("over", "") or ""
            # for_each에서 인덱스 추출
            import re as _re
            fe_indices = set(_re.findall(r'(\w+)\s+in\s+(\w+)', for_each))
            ov_indices = set(_re.findall(r'(\w+)\s+in\s+(\w+)', sum_over))
            
            # 동일 set으로 for_each와 over를 모두 사용하는 경우
            fe_sets = {s for _, s in fe_indices}
            ov_sets = {s for _, s in ov_indices}
            overlap = fe_sets & ov_sets
            if overlap:
                errors.append(
                    f"Constraint '{cname}': for_each와 sum.over가 동일 set {overlap}을 사용 → "
                    f"O(n²) 폭발. for_each의 인덱스 변수와 sum의 인덱스 변수를 분리해야 함"
                )
        
        # 검증2: sum의 coeff에 param이 있는데 해당 param의 source_file이 비정형 원본인 경우
        if isinstance(lhs, dict) and "sum" in lhs and isinstance(lhs["sum"], dict):
            coeff = lhs["sum"].get("coeff")
            if isinstance(coeff, dict) and "param" in coeff:
                param_ref = coeff["param"]
                pname = param_ref if isinstance(param_ref, str) else param_ref.get("name", "")
                # 해당 파라미터의 source 확인
                for p in parameters:
                    if p.get("name") == pname:
                        src_col = p.get("source_column", "") or ""
                        if src_col.startswith("Unnamed"):
                            errors.append(
                                f"Constraint '{cname}': coeff param '{pname}'이 "
                                f"비정형 컬럼 '{src_col}'에 매핑됨 → 데이터 바인딩 실패 예상"
                            )
                        break
    
    # 검증3: source_file이 없는 파라미터 중 default도 없는 것
    unbound_params = []
    for p in parameters:
        pname = p.get("name", "")
        src_file = p.get("source_file") or ""
        src_col = p.get("source_column") or ""
        default_val = p.get("default_value", p.get("default"))
        
        if not src_file and not src_col and default_val is None:
            unbound_params.append(pname)
    
    # ★ 데이터 컬럼 보호: trips.csv 컬럼은 절대 user_input_required 금지
    _protected_columns = set()
    if dataframes:
        for _dk, _dv in dataframes.items():
            if "trips" in _dk.lower():
                _protected_columns.update(str(c).strip() for c in _dv.columns)
    logger.info(f"Protected data columns: {_protected_columns}")

    # ★ Phase 0: confirmed_problem 우선 바인딩 (이름 유사도 매칭 포함)
    _all_model_params = math_model.get("parameters", [])
    if confirmed_problem:
        _cp_params = confirmed_problem.get("parameters", {})
        if isinstance(_cp_params, dict) and _cp_params:
            _cp_map = {}
            for _cpk, _cpv in _cp_params.items():
                _val = _cpv.get("value") if isinstance(_cpv, dict) else _cpv
                if _val is not None:
                    _cp_map[_cpk] = _val
                    _cp_map[_cpk.lower()] = _val

            _cp_bound_phase0 = 0
            for p in _all_model_params:
                pid = p.get("id", "")
                pname = p.get("name", "")
                sf = p.get("source_file") or ""
                dv = p.get("default_value", p.get("default"))
                if (sf and sf != "None") or dv is not None:
                    continue
                # 데이터 컬럼이면 스킵
                if pid in _protected_columns or pname in _protected_columns:
                    continue

                # 정확 매칭
                matched_val = None
                matched_via = None
                for candidate in [pid, pname, pid.lower(), pname.lower()]:
                    if candidate and candidate in _cp_map:
                        matched_val = _cp_map[candidate]
                        matched_via = candidate
                        break

                # 유사도 매칭 → suggestion only (실행 경로에 저장 금지)
                _similarity_match = None
                _similarity_via = None
                if matched_val is None and (pid or pname):
                    best_score = 0
                    for cpk, cpv in _cp_map.items():
                        for candidate in [pid.lower(), pname.lower()]:
                            if not candidate:
                                continue
                            score = _token_similarity(candidate, cpk.lower())
                            if score > best_score and score >= 0.65:
                                best_score = score
                                _similarity_match = cpv
                                _similarity_via = f"{cpk} (similarity={score:.2f})"

                if matched_val is not None:
                    # 정확 매칭 → math_model에 저장 (안전)
                    try:
                        p["default_value"] = float(matched_val)
                    except (ValueError, TypeError):
                        p["default_value"] = matched_val
                    p["auto_bound"] = True
                    p["auto_bound_source"] = "confirmed_problem"
                    p.pop("user_input_required", None)
                    _cp_bound_phase0 += 1
                    logger.info(f"Phase0-CP-bind: {pid or pname} = {matched_val} (via {matched_via})")
                elif _similarity_match is not None:
                    # 유사도 매칭 → suggestion으로만 격리 (math_model에 저장 안 함)
                    corrections.setdefault("suggestions", []).append({
                        "param_id": pid or pname,
                        "suggested_value": _similarity_match,
                        "source": _similarity_via,
                        "reason": "token_similarity",
                    })
                    logger.info(f"Phase0-CP-suggest: {pid or pname} = {_similarity_match} (SUGGESTION, via {_similarity_via})")
            logger.info(f"Phase0 confirmed_problem bind: {_cp_bound_phase0} params")

    # ★ Phase 0b: 데이터 컬럼 파라미터 자동 해결
    for p in _all_model_params:
        pid = p.get("id", "")
        pname = p.get("name", "")
        if pid in _protected_columns or pname in _protected_columns:
            # 이 파라미터는 데이터 컬럼이므로 source 설정
            col_name = pid if pid in _protected_columns else pname
            if not p.get("source_file"):
                p["source_file"] = "normalized/trips.csv"
                p["source_column"] = col_name
                p.pop("user_input_required", None)
                logger.info(f"Phase0b-column-bind: {pid or pname} -> trips.csv/{col_name}")

    # --- 정규화 파라미터에서 직접 바인딩 ---
    if dataframes:
        _norm_params_df = None
        for _dk, _dv in dataframes.items():
            if _dk.startswith("normalized/") and "param_name" in _dv.columns and "value" in _dv.columns:
                _norm_params_df = _dv
                break

        if _norm_params_df is not None:
            _available = {}
            for _, _row in _norm_params_df.iterrows():
                _pn = str(_row["param_name"]).strip()
                _pv = _row["value"]
                _available[_pn] = _pv
                _available[_pn.lower()] = _pv
                # Also index by semantic_id for matching
                _sid = _row.get('semantic_id', '')
                if _sid:
                    _available[_sid] = _pv
                    _available[_sid.lower()] = _pv

            # 모델 파라미터에서 id, name 모두 수집하여 매핑 테이블 구축
            _bound_count = 0

            for p in _all_model_params:
                pid = p.get("id", "")
                pname = p.get("name", "")
                sf = p.get("source_file") or ""
                dv = p.get("default_value", p.get("default"))

                # 이미 바인딩된 파라미터는 스킵
                if (sf and sf != "None") or dv is not None:
                    continue

                # 매칭 시도: id -> name -> id.lower -> name.lower
                matched_val = None
                matched_key = None
                for candidate in [pid, pname, pid.lower(), pname.lower()]:
                    if candidate and candidate in _available:
                        matched_val = _available[candidate]
                        matched_key = candidate
                        break

                if matched_val is not None:
                    try:
                        p["default_value"] = float(matched_val)
                    except (ValueError, TypeError):
                        p["default_value"] = matched_val
                    p["auto_bound"] = True
                    p["auto_bound_source"] = "normalized/parameters.csv"
                    if "user_input_required" in p:
                        del p["user_input_required"]
                    corrections.setdefault("direct_bind", []).append(
                        f"{pid or pname} = {matched_val} (matched via '{matched_key}')"
                    )
                    _bound_count += 1
                    logger.info(f"Direct-bind: {pid or pname} = {matched_val} (key='{matched_key}')")

            logger.info(f"Direct-bind complete: {_bound_count} params bound from normalized/parameters.csv")

            # ── Phase 2: Alias-based binding (Korean hints → English param IDs) ──
            _alias_map = _build_param_alias_map()
            if _alias_map:
                # Build reverse map: param_id → list of Korean hints
                _pid_to_hints = {}
                for _hint, _target_pid in _alias_map.items():
                    _pid_to_hints.setdefault(_target_pid, []).append(_hint)

                # Scan normalized parameters for alias matches
                for p in _all_model_params:
                    pid = p.get("id", "")
                    pname = p.get("name", "")
                    sf = p.get("source_file") or ""
                    dv = p.get("default_value", p.get("default"))
                    if (sf and sf != "None") or dv is not None:
                        continue  # already bound

                    # Get Korean hints for this param ID
                    hints_for_pid = _pid_to_hints.get(pid, []) + _pid_to_hints.get(pname, [])
                    if not hints_for_pid:
                        continue

                    # Search parameters.csv rows for hint matches
                    best_match_val = None
                    best_match_key = None
                    best_score = 0
                    for _, _row in _norm_params_df.iterrows():
                        _pn = str(_row["param_name"]).strip()
                        _pv = _row["value"]
                        if not _pn:
                            continue
                        for _hint in hints_for_pid:
                            if _hint in _pn or _pn in _hint:
                                _score = len(_hint)
                                if _score > best_score:
                                    best_score = _score
                                    best_match_val = _pv
                                    best_match_key = f"{_pn} (alias: {_hint})"

                    if best_match_val is not None:
                        try:
                            p["default_value"] = float(best_match_val)
                        except (ValueError, TypeError):
                            p["default_value"] = best_match_val
                        p["auto_bound"] = True
                        p["auto_bound_source"] = "normalized/parameters.csv (alias)"
                        if "user_input_required" in p:
                            del p["user_input_required"]
                        corrections.setdefault("alias_bind", []).append(
                            f"{pid or pname} = {best_match_val} (via {best_match_key})"
                        )
                        _bound_count += 1
                        logger.info(f"Alias-bind: {pid or pname} = {best_match_val} ({best_match_key})")

            logger.info(f"Direct+Alias bind complete: {_bound_count} params bound")

    # --- dedup: remove unnecessary parameters ---
    _remove_ids = set()
    _all_pids = {p.get("id",""): p for p in math_model.get("parameters", [])}
    for _pid, _pdata in _all_pids.items():
        # 1) trips.csv 컬럼 제거
        _src = str(_pdata.get("source_file","")) + str(_pdata.get("auto_bound_source",""))
        if "trips.csv" in _src and not _pdata.get("default_value") and not _pdata.get("value"):
            _remove_ids.add(_pid)
        # 2) 세부 파라미터가 있는 기본 파라미터 제거 (preparation_minutes, cleanup_minutes)
        if _pid in ("preparation_minutes", "cleanup_minutes"):
            _has_detail = any(k.startswith(_pid + "_") for k in _all_pids)
            if _has_detail:
                _remove_ids.add(_pid)
        # 3) 축약 이름 중복 제거 (_time_ vs _minutes_)
        if "_time_" in _pid:
            _full = _pid.replace("_time_", "_minutes_")
            if _full in _all_pids:
                _remove_ids.add(_pid)
    if _remove_ids:
        math_model["parameters"] = [p for p in math_model["parameters"] if p.get("id","") not in _remove_ids]
        logger.info(f"Dedup removed {len(_remove_ids)} params: {_remove_ids}")


    # --- confirmed_problem 기반 fallback 매핑 (Phase0에서 이미 처리, 잔여분만) ---
    if unbound_params and confirmed_problem:
        _cp_params = confirmed_problem.get("parameters", {})
        if isinstance(_cp_params, dict) and _cp_params:
            # confirmed_problem param key -> value 맵
            _cp_map = {}
            for _cpk, _cpv in _cp_params.items():
                _val = _cpv.get("value") if isinstance(_cpv, dict) else _cpv
                _cp_map[_cpk] = _val
                _cp_map[_cpk.lower()] = _val

            _all_model_params2 = math_model.get("parameters", [])
            _cp_bound = 0
            for p in _all_model_params2:
                pid = p.get("id", "")
                pname = p.get("name", "")
                dv = p.get("default_value", p.get("default"))
                sf = p.get("source_file") or ""

                if (sf and sf != "None") or dv is not None:
                    continue

                # pid/pname으로 confirmed_problem 매칭
                matched_val = None
                matched_via = None
                for candidate in [pid, pname, pid.lower(), pname.lower()]:
                    if candidate and candidate in _cp_map:
                        matched_val = _cp_map[candidate]
                        matched_via = candidate
                        break

                if matched_val is not None:
                    try:
                        p["default_value"] = float(matched_val)
                    except (ValueError, TypeError):
                        p["default_value"] = matched_val
                    p["auto_bound"] = True
                    p["auto_bound_source"] = "confirmed_problem"
                    if "user_input_required" in p:
                        del p["user_input_required"]
                    corrections.setdefault("cp_bind", []).append(
                        f"{pid or pname} = {matched_val} (from confirmed_problem via '{matched_via}')"
                    )
                    _cp_bound += 1
                    logger.info(f"CP-bind: {pid or pname} = {matched_val} (via '{matched_via}')")

            # unbound 재계산
            unbound_params = []
            for p in _all_model_params2:
                pn = p.get("name", p.get("id", ""))
                sf2 = p.get("source_file") or ""
                sc2 = p.get("source_column") or ""
                dv2 = p.get("default_value", p.get("default"))
                if not sf2 and not sc2 and dv2 is None:
                    unbound_params.append(pn)
            logger.info(f"CP-bind: {_cp_bound} bound, {len(unbound_params)} still unbound: {unbound_params}")

    # --- reference_ranges.yaml fallback (midpoint of typical range) ---
    if unbound_params:
        try:
            import os as _os
            import yaml as _yaml
            _base = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            # 도메인을 math_model 또는 confirmed_problem에서 동적으로 결정
            _domain = math_model.get("domain", "")
            if not _domain and confirmed_problem:
                _domain = confirmed_problem.get("domain", "")
            if not _domain:
                _domain = "railway"  # legacy fallback
            _ref_path = _os.path.join(_base, "knowledge", "domains", _domain, "reference_ranges.yaml")
            if _os.path.isfile(_ref_path):
                with open(_ref_path, "r", encoding="utf-8") as _rf:
                    _ref_data = _yaml.safe_load(_rf) or {}
                # Flatten all sub-domain ranges into a single lookup
                _ref_flat = {}
                for _subdomain, _subdata in _ref_data.items():
                    if not isinstance(_subdata, dict):
                        continue
                    for _rk, _rv in _subdata.items():
                        if isinstance(_rv, dict) and "range" in _rv:
                            _rng = _rv["range"]
                            if isinstance(_rng, list) and len(_rng) == 2:
                                _midpoint = round((_rng[0] + _rng[1]) / 2, 2)
                                if _rk not in _ref_flat:
                                    _ref_flat[_rk] = _midpoint
                _ref_bound = 0
                _all_model_params3 = math_model.get("parameters", [])
                for p in _all_model_params3:
                    pid = p.get("id", "")
                    pname = p.get("name", "")
                    dv = p.get("default_value", p.get("default"))
                    sf = p.get("source_file") or ""
                    if (sf and sf != "None") or dv is not None:
                        continue
                    for candidate in [pid, pname, pid.lower(), pname.lower()]:
                        if candidate and candidate in _ref_flat:
                            p["default_value"] = _ref_flat[candidate]
                            p["auto_bound"] = True
                            p["auto_bound_source"] = f"reference_ranges.yaml (midpoint)"
                            if "user_input_required" in p:
                                del p["user_input_required"]
                            corrections.setdefault("ref_range_bind", []).append(
                                f"{pid or pname} = {_ref_flat[candidate]} (reference midpoint)"
                            )
                            _ref_bound += 1
                            logger.info(f"Ref-range bind: {pid or pname} = {_ref_flat[candidate]}")
                            break
                # Update unbound list
                unbound_params = []
                for p in _all_model_params3:
                    pn = p.get("name", p.get("id", ""))
                    sf2 = p.get("source_file") or ""
                    sc2 = p.get("source_column") or ""
                    dv2 = p.get("default_value", p.get("default"))
                    if not sf2 and not sc2 and dv2 is None:
                        unbound_params.append(pn)
                logger.info(f"Ref-range bind: {_ref_bound} bound, {len(unbound_params)} still unbound: {unbound_params}")
        except Exception as _e:
            logger.warning(f"Reference range fallback failed: {_e}")

    # --- Big M 자동 계산 (데이터 기반) ---
    _bigm_names = {"big_m", "bigm", "big m", "빅 m", "빅m"}
    _needs_bigm = any(
        (p.get("id", "").lower() in _bigm_names or p.get("name", "").lower() in _bigm_names)
        and p.get("default_value", p.get("default")) is None
        and not (p.get("source_file") or "")
        for p in math_model.get("parameters", [])
    )
    if _needs_bigm and dataframes:
        # Big M 계산: 야간→새벽 커버를 고려한 duty 시간 범위
        # 야간 근무자가 자정을 넘겨 새벽 열차를 커버하면
        # duty 시간이 1440분(24시간)을 초과할 수 있음
        # Big M = 1440 + max(arr_time) 으로 설정 (익일 도착까지 커버)
        _dep_cols = {"trip_dep_time", "dep_time", "departure_time", "start_time"}
        _arr_cols = {"trip_arr_time", "arr_time", "arrival_time", "end_time"}
        _dep_times = []
        _arr_times = []
        for _dk, _df in dataframes.items():
            for _col in _df.columns:
                _cl = _col.lower()
                try:
                    _vals = _df[_col].dropna().astype(float).tolist()
                except (ValueError, TypeError):
                    continue
                if _cl in _dep_cols or any(tc in _cl for tc in _dep_cols):
                    _dep_times.extend(_vals)
                elif _cl in _arr_cols or any(tc in _cl for tc in _arr_cols):
                    _arr_times.extend(_vals)
        _all_times = _dep_times + _arr_times
        if _all_times:
            _max_time = max(_all_times)
            _min_time = min(_all_times)
            # 자정 넘김 감지: arr_time에 0 근처 값이 있으면 야간→새벽 패턴
            _has_midnight_wrap = _arr_times and min(_arr_times) < 360  # 06:00 이전 도착
            if _has_midnight_wrap:
                # 익일 새벽 도착까지 커버: 1440 + max(새벽 도착시간) + 여유
                _early_arrivals = [t for t in _arr_times if t < 360]
                _bigm_val = 1440 + max(_early_arrivals) + 120  # 2시간 여유
            else:
                _bigm_val = _max_time - _min_time + 120
            _bigm_val = max(_bigm_val, 1440)  # 최소 24시간(분)
            _bigm_reason = (
                f"Big M = {'1440 + max(새벽도착) + 120' if _has_midnight_wrap else 'time_range + 120'}"
                f" = {int(_bigm_val)}분 ({'야간→새벽 커버 감지' if _has_midnight_wrap else '일반'})"
            )
        else:
            _bigm_val = 1440  # fallback: 24시간(분)
            _bigm_reason = "Big M = 1440분 (fallback: 24시간)"

        for p in math_model.get("parameters", []):
            _pid = p.get("id", "")
            _pname = p.get("name", "")
            if (_pid.lower() in _bigm_names or _pname.lower() in _bigm_names) \
                    and p.get("default_value", p.get("default")) is None \
                    and not (p.get("source_file") or ""):
                p["default_value"] = int(_bigm_val)
                p["auto_bound"] = True
                p["auto_bound_source"] = "auto_calculated (time_range)"
                p["auto_bound_reasoning"] = _bigm_reason
                p.pop("user_input_required", None)
                if _pid in [u for u in unbound_params]:
                    unbound_params.remove(_pid)
                elif _pname in [u for u in unbound_params]:
                    unbound_params.remove(_pname)
                corrections.setdefault("bigm_auto", []).append(
                    f"{_pid or _pname} = {int(_bigm_val)} (auto: time range)"
                )
                logger.info(f"Big-M auto-calc: {_pid or _pname} = {int(_bigm_val)} (time range + 60)")

    # --- 범용 자동 바인딩 시도 ---
    if unbound_params and dataframes:
        auto_corrections, remaining_unbound = auto_bind_unbound_parameters(
            math_model, dataframes
        )
        if auto_corrections:
            corrections["auto_bind"] = auto_corrections
        if remaining_unbound:
            warnings.append(
                f"자동 바인딩 실패 파라미터 {len(remaining_unbound)}개 "
                f"(user_input_required로 마킹됨): {remaining_unbound[:5]}"
            )
        unbound_params = remaining_unbound  # 갱신


    # ★ 최종 보호: 데이터 컬럼 파라미터의 user_input_required 제거
    for p in math_model.get("parameters", []):
        pid = p.get("id", "")
        pname = p.get("name", "")
        if (pid in _protected_columns or pname in _protected_columns) and p.get("user_input_required"):
            p.pop("user_input_required", None)
            if not p.get("source_file"):
                p["source_file"] = "normalized/trips.csv"
                p["source_column"] = pid if pid in _protected_columns else pname
            logger.info(f"Final-protect: {pid or pname} removed from user_input_required")

    # ★ 동적 중복 검출: 1계층(auto_injected) 파라미터 기준
    _layer1_ids = set()
    for p in math_model.get("parameters", []):
        if p.get("auto_injected") or p.get("layer") == 1:
            _layer1_ids.add(p.get("id", "").lower())
    
    # LLM이 생성한 파라미터 중 1계층과 id가 동일한 것 검출
    _duplicate_found = []
    for p in math_model.get("parameters", []):
        if p.get("auto_injected") or p.get("layer") == 1:
            continue
        pid = (p.get("id") or "").lower()
        if pid and pid in _layer1_ids:
            _duplicate_found.append(p.get("id", ""))
    
    if _duplicate_found:
        errors.append(
            f"1계층과 id가 중복되는 파라미터 발견 (재생성 필요): {_duplicate_found}. "
            f"시스템이 자동 주입하는 파라미터와 동일한 id를 사용하지 마세요."
        )
        logger.warning(f"Duplicate layer-1 params detected: {_duplicate_found}")

    # ★ 2계층 파라미터 분류
    _layer2_need_input = []
    for p in math_model.get("parameters", []):
        if p.get("auto_injected") or p.get("auto_bound") or p.get("layer") == 1:
            continue
        pid = p.get("id", "")
        sf = p.get("source_file") or ""
        dv = p.get("default_value", p.get("default", p.get("value")))
        if (not sf or sf == "None") and dv is None:
            _layer2_need_input.append(pid)
            p["user_input_required"] = True
            p["layer"] = 2
    
    if _layer2_need_input:
        logger.info(f"Layer-2 params needing user input ({len(_layer2_need_input)}): {_layer2_need_input}")


    if len(unbound_params) > 3:
        warnings.append(
            f"미바인딩 파라미터 {len(unbound_params)}개 (3개 초과): "
            f"{unbound_params[:5]}... → 솔버 실행 시 모두 0으로 처리되어 INFEASIBLE 가능"
        )
    elif unbound_params:
        warnings.append(
            f"미바인딩 파라미터 {len(unbound_params)}개: {unbound_params}"
        )
    
    # 검증5: binary 변수와 큰 상수/파라미터를 직접 비교하는 무효 제약 감지
    # 예: x[j,i] >= 360 (binary는 0 or 1이므로 1보다 큰 값과 비교 불가)
    var_types = {v.get("id", ""): v.get("type", "binary") for v in math_model.get("variables", [])}
    
    for con in math_model.get("constraints", []):
        cname = con.get("name", "unknown")
        lhs = con.get("lhs", {})
        rhs = con.get("rhs", {})
        op = con.get("operator", "")
        
        if not isinstance(lhs, dict) or not isinstance(rhs, dict):
            continue

        # 한쪽이 단일 binary 변수이고 다른 쪽이 상수/파라미터인지 체크
        def _is_single_binary_var(node):
            """노드가 단일 binary 변수 참조인지"""
            if "var" in node:
                var_ref = node["var"]
                if isinstance(var_ref, dict):
                    vname = var_ref.get("name", "")
                    return var_types.get(vname, "binary") == "binary"
                elif isinstance(var_ref, str):
                    return var_types.get(var_ref, "binary") == "binary"
            return False

        def _get_scalar_value(node, param_defaults):
            """노드가 스칼라 상수이면 그 값을, 파라미터이면 default_value를 반환"""
            if "value" in node:
                v = node["value"]
                if isinstance(v, (int, float)):
                    return v
            if "param" in node:
                p = node["param"]
                pname = p.get("name", "") if isinstance(p, dict) else str(p)
                return param_defaults.get(pname)
            return None

        # 파라미터 default 맵
        param_defaults = {}
        for p in math_model.get("parameters", []):
            pid = p.get("id", p.get("name", ""))
            dv = p.get("default_value", p.get("default"))
            if dv is not None:
                try:
                    param_defaults[pid] = float(dv)
                except (ValueError, TypeError):
                    pass

        # Case A: lhs=binary var, rhs=상수/파라미터
        if _is_single_binary_var(lhs):
            rhs_val = _get_scalar_value(rhs, param_defaults)
            if rhs_val is not None and rhs_val > 1:
                if op in (">=", ">"):
                    errors.append(
                        f"Constraint '{cname}': binary 변수(0/1)에 {op} {rhs_val} 비교 — "
                        f"항상 불만족 (INFEASIBLE 원인). "
                        f"시간/값 제약은 sum 또는 별도 연속변수로 표현 필요"
                    )
                elif op in ("<=", "<"):
                    warnings.append(
                        f"Constraint '{cname}': binary 변수(0/1)에 {op} {rhs_val} 비교 — "
                        f"항상 만족하여 무의미한 제약"
                    )
            elif rhs_val is None and "param" in rhs:
                # default가 없어도 binary var와 param 직접 비교는 거의 항상 오류
                rhs_param = rhs["param"]
                rhs_pname = rhs_param.get("name", "") if isinstance(rhs_param, dict) else str(rhs_param)
                # sum이 아닌 단일 var와 param 비교이면 오류
                if "sum" not in lhs:
                    errors.append(
                        f"Constraint '{cname}': binary 변수(0/1)를 파라미터 '{rhs_pname}'와 직접 비교 — "
                        f"binary 변수는 0 또는 1만 가능. "
                        f"시간/값 제약은 sum(coeff*var) 또는 별도 연속변수(integer/continuous)로 표현 필요"
                    )

        # Case B: rhs=binary var, lhs=상수/파라미터
        if _is_single_binary_var(rhs):
            lhs_val = _get_scalar_value(lhs, param_defaults)
            if lhs_val is not None and lhs_val > 1:
                if op in ("<=", "<"):
                    errors.append(
                        f"Constraint '{cname}': {lhs_val} {op} binary 변수(0/1) — "
                        f"항상 불만족 (INFEASIBLE 원인)"
                    )
                elif op in (">=", ">"):
                    warnings.append(
                        f"Constraint '{cname}': {lhs_val} {op} binary 변수(0/1) — "
                        f"항상 만족하여 무의미한 제약"
                    )
            elif lhs_val is None and "param" in lhs:
                lhs_param = lhs["param"]
                lhs_pname = lhs_param.get("name", "") if isinstance(lhs_param, dict) else str(lhs_param)
                if "sum" not in rhs:
                    errors.append(
                        f"Constraint '{cname}': 파라미터 '{lhs_pname}'를 binary 변수(0/1)와 직접 비교 — "
                        f"binary 변수는 0 또는 1만 가능. "
                        f"시간/값 제약은 sum(coeff*var) 또는 별도 연속변수(integer/continuous)로 표현 필요"
                    )

    # 검증6: 양쪽 모두 변수가 없는 제약 (param vs param, value vs value)
    for con in math_model.get("constraints", []):
        cname = con.get("name", "unknown")
        lhs = con.get("lhs", {})
        rhs = con.get("rhs", {})
        
        if not isinstance(lhs, dict) or not isinstance(rhs, dict):
            continue

        def _has_var(node):
            """노드 트리에 변수 참조가 있는지"""
            if not isinstance(node, dict):
                return False
            if "var" in node:
                return True
            if "sum" in node:
                s = node["sum"]
                if isinstance(s, dict) and "var" in s:
                    return True
            if "multiply" in node:
                return any(_has_var(n) for n in node["multiply"] if isinstance(n, dict))
            if "add" in node:
                return any(_has_var(n) for n in node["add"] if isinstance(n, dict))
            if "subtract" in node:
                return any(_has_var(n) for n in node["subtract"] if isinstance(n, dict))
            return False

        if not _has_var(lhs) and not _has_var(rhs):
            warnings.append(
                f"Constraint '{cname}': 양쪽에 의사결정 변수가 없음 — "
                f"데이터 검증일 뿐 최적화에 영향 없음 (제거 권장)"
            )


    # 검증4: Set이 Unnamed 컬럼을 source로 사용하는 경우
    for s in sets:
        sid = s.get("id", s.get("name", ""))
        src_col = s.get("source_column") or ""
        if src_col.startswith("Unnamed"):
            errors.append(
                f"Set '{sid}': 비정형 컬럼 '{src_col}'에서 값을 가져옴 → "
                f"블록 파서 결과(__summary)를 사용해야 함"
            )


    is_valid = len(errors) == 0

    # -- 상세 로그 출력 --
    if errors:
        for i, e in enumerate(errors):
            logger.error(f"Gate2 error  [{i+1}/{len(errors)}]: {e}")
    if warnings:
        for i, w in enumerate(warnings):
            logger.warning(f"Gate2 warn   [{i+1}/{len(warnings)}]: {w}")


    # project_id 추출 (uploads 경로에서)
    _struct_project_id = None
    if dataframes:
        import re as _re
        for _df_key in dataframes:
            _m = _re.search(r'uploads[/\\](\d+)', str(_df_key))
            if _m:
                _struct_project_id = _m.group(1)
                break
    if _struct_project_id is None:
        # model에서 추출 시도
        for _s in math_model.get("sets", []):
            _sf = _s.get("source_file", "")
            if "normalized/" in _sf:
                import os as _os2
                for _d in sorted(_os2.listdir("uploads"), key=lambda x: -int(x) if x.isdigit() else 0):
                    if _d.isdigit() and _os2.path.exists(f"uploads/{_d}/normalized"):
                        _struct_project_id = _d
                        break
                break
    logger.info(f"Struct validation: project_id={_struct_project_id}")

    # ★ 구조 검증: for_each 누락, param index 누락 자동 교정
    # 템플릿 기반 모델은 구조가 이미 검증되어 있으므로 struct fix 스킵
    _skip_struct = math_model.get("metadata", {}).get("skip_struct_fix", False)
    if _skip_struct:
        logger.info("Struct validation: SKIPPED (template-based model)")
        struct_fixes = 0
    else:
        struct_fixes = _fix_constraint_structure(math_model, corrections, warnings, set_sizes=set_sizes, project_id=_struct_project_id)
    if struct_fixes > 0:
        corrections['structural_fixes'] = struct_fixes

    # struct fix 후 deferred set error 재검증
    # struct fix에서 overlap_pairs 등이 주입되었으면 error 해소
    if _deferred_set_errors:
        for deferred_err in _deferred_set_errors:
            # set_sizes가 struct fix에서 갱신되었는지 확인
            for s in sets:
                sid = s.get("id", "")
                if sid in _AUTO_INJECTABLE_SETS and f"'{sid}'" in deferred_err:
                    new_size = _validate_set(s, data_profile, dataframes)
                    if new_size > 0 or s.get("_overlap_pairs"):
                        # struct fix에서 주입됨 → warning으로 전환
                        warnings.append(deferred_err.replace("크기를 결정할 수 없음", "struct fix로 주입됨"))
                    else:
                        # 여전히 미해결 → error 유지
                        errors.append(deferred_err)

    result = {
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "corrections": corrections,
        "actual_variable_count": actual_var_count,
        "actual_set_sizes": set_sizes,
    }

    logger.info(
        f"Gate2: valid={is_valid}, errors={len(errors)}, "
        f"warnings={len(warnings)}, corrections={len(corrections)}, "
        f"actual_vars={actual_var_count}"
    )

    return result


def _validate_set(set_def: Dict,
                  data_profile: Optional[Dict],
                  dataframes: Optional[Dict]) -> int:
    """Set 크기를 결정"""
    # 1. source_type: "range"
    if set_def.get("source_type") == "range":
        size = set_def.get("size", 0)
        if size > 0:
            return size

    # 2. elements가 있으면
    elements = set_def.get("elements", [])
    if elements:
        return len(elements)

    # 3. source_type: "explicit" + values
    values = set_def.get("values", [])
    if values:
        return len(values)

    # 4. source_file + source_column — dataframes에서 직접 계산
    source_file = set_def.get("source_file", "")
    source_col = set_def.get("source_column", "")
    if source_file and source_col and dataframes:
        for key, df in dataframes.items():
            if source_file in key or key.startswith(source_file):
                if source_col in df.columns:
                    return int(df[source_col].dropna().nunique())
                # 대소문자 무시 매칭
                for col in df.columns:
                    if col.strip().lower() == source_col.strip().lower():
                        set_def["source_column"] = col  # 자동 교정
                        return int(df[col].dropna().nunique())
                # ★ fuzzy match: dep_station -> start_station 등
                fuzzy = _fuzzy_match_column(source_col, set(df.columns))
                if fuzzy:
                    logger.info(f"Set fuzzy fix: '{source_col}' -> '{fuzzy}'")
                    set_def["source_column"] = fuzzy
                    return int(df[fuzzy].dropna().nunique())

    # 5. data_profile에서 추정
    if data_profile and source_file and source_col:
        for sheet_key, sheet_info in data_profile.get("files", {}).items():
            if source_file in sheet_key:
                col_info = sheet_info.get("columns", {}).get(source_col, {})
                if col_info:
                    return col_info.get("unique_count", 0)

    return 0


def _check_node_refs(node: Any, cname: str, side: str,
                     var_ids: set, param_names: set,
                     warnings: List[str], corrections: Dict[str, Any],
                     data_col_names: set = None):
    """노드에서 참조하는 변수/파라미터가 정의되어 있는지 재귀 검사"""
    if not isinstance(node, dict):
        return
    if data_col_names is None:
        data_col_names = set()

    # var 참조
    if "var" in node:
        var_ref = node["var"] if isinstance(node["var"], str) else node["var"].get("name", "")
        if var_ref and var_ref not in var_ids:
            warnings.append(
                f"Constraint '{cname}' {side}: variable '{var_ref}' 미정의"
            )

    # param 참조
    if "param" in node:
        param_node = node["param"]
        if isinstance(param_node, str):
            param_ref = param_node
            has_source = False
        else:
            param_ref = param_node.get("name", "")
            has_source = bool(param_node.get("source_column") or param_node.get("source_file"))
        # 데이터 컬럼명과 일치하면 데이터 참조이므로 경고 불필요
        is_data_col = param_ref in data_col_names
        is_fuzzy_col = False
        fuzzy_matched = None
        if param_ref and not is_data_col and data_col_names and param_ref not in param_names:
            fuzzy_matched = _fuzzy_match_column(param_ref, data_col_names)
            if fuzzy_matched:
                is_fuzzy_col = True
                corrections[f"param_{param_ref}"] = {
                    "type": "column_name_fix",
                    "old": param_ref,
                    "new": fuzzy_matched,
                    "location": f"{cname}.{side}"
                }
        if param_ref and param_ref not in param_names and not has_source and not is_data_col and not is_fuzzy_col:
            warnings.append(
                f"Constraint '{cname}' {side}: parameter '{param_ref}' 미정의"
            )

    # sum 노드 내부
    if "sum" in node:
        sum_node = node["sum"]
        if isinstance(sum_node, dict):
            if "coeff" in sum_node and sum_node["coeff"]:
                _check_node_refs(sum_node["coeff"], cname, side, var_ids, param_names, warnings, corrections, data_col_names)

    # subtract, add, multiply 노드
    for op_key in ["subtract", "add", "multiply"]:
        if op_key in node:
            sub = node[op_key]
            if isinstance(sub, list):
                for item in sub:
                    _check_node_refs(item, cname, side, var_ids, param_names, warnings, corrections, data_col_names)
            elif isinstance(sub, dict):
                _check_node_refs(sub, cname, side, var_ids, param_names, warnings, corrections, data_col_names)


def _fuzzy_match_column(target: str, available: set) -> Optional[str]:
    """유사 컬럼명 매칭 (exact -> substring -> token overlap)"""
    target_low = target.strip().lower()
    avail_list = [c for c in available if c]

    # Semantic aliases: LLM이 자주 사용하는 이름 -> 실제 컬럼명
    _semantic_aliases = {
        "travel_time": "trip_duration",
        "travel_time_min": "trip_duration",
        "travel_time_minutes": "trip_duration",
        "run_time": "trip_duration",
        "running_time": "trip_duration",
        "dep_time": "trip_dep_time",
        "dep_time_min": "trip_dep_time",
        "departure_time": "trip_dep_time",
        "arr_time": "trip_arr_time",
        "arr_time_min": "trip_arr_time",
        "arrival_time": "trip_arr_time",
    }
    alias_match = _semantic_aliases.get(target_low)
    if alias_match and alias_match in [c.strip().lower() for c in avail_list]:
        for col in avail_list:
            if col.strip().lower() == alias_match:
                return col


    # 1. exact match
    for col in avail_list:
        if col.strip().lower() == target_low:
            return col

    # 2. substring match
    for col in avail_list:
        cl = col.strip().lower()
        if target_low in cl or cl in target_low:
            return col

    # 3. token overlap (split by _ and compare)
    import re as _re
    target_tokens = set(_re.split(r"[_\s]+", target_low))
    best_col = None
    best_score = 0
    for col in avail_list:
        col_tokens = set(_re.split(r"[_\s]+", col.strip().lower()))
        overlap = len(target_tokens & col_tokens)
        if overlap > best_score:
            best_score = overlap
            best_col = col
    if best_score >= 2:
        return best_col

    return None


def to_text_summary(result: Dict) -> str:
    """Gate 2 결과를 읽기 쉬운 텍스트로 변환"""
    lines = [f"[모델 검증 결과] valid={result['valid']}"]
    lines.append(f"실제 변수 수: {result['actual_variable_count']}")
    lines.append(f"Set 크기: {result['actual_set_sizes']}")

    if result["errors"]:
        lines.append(f"\n❌ 오류 ({len(result['errors'])}개):")
        for e in result["errors"]:
            lines.append(f"  - {e}")

    if result["warnings"]:
        lines.append(f"\n⚠ 경고 ({len(result['warnings'])}개):")
        for w in result["warnings"]:
            lines.append(f"  - {w}")

    if result["corrections"]:
        lines.append(f"\n🔧 자동 교정 ({len(result['corrections'])}개):")
        for key, val in result["corrections"].items():
            if key == "auto_bind" and isinstance(val, list):
                for ab in val:
                    lines.append(f"  - [자동바인딩] {ab}")
                continue
            lines.append(f"  - {key}: {val['old']} → {val['new']} ({val['reason']})")

    return "\n".join(lines)


def _fix_constraint_structure(model: Dict, corrections: Dict, warnings: List[str], set_sizes: Dict[str, int] = None, project_id: Any = None):
    """
    Gate2 구조 검증: LLM이 생성한 제약식의 구조적 결함을 자동 교정.
    1) source_file이 있는 param에 index 누락 -> 추가
    2) for_each에 없는 루프변수가 제약 내부에서 참조됨 -> 추가
       단, sum.over에 이미 선언된 변수는 제외 (sum 내부에서 처리되므로)
    """
    import json as _json

    constraints = model.get("constraints", [])
    sets_by_id = {}
    for s in model.get("sets", []):
        sets_by_id[s.get("id", "")] = s

    fix_count = 0

    def collect_sum_over_vars(node):
        """sum.over에 선언된 루프 변수를 수집"""
        result = set()
        if not isinstance(node, dict):
            return result
        if "sum" in node and isinstance(node["sum"], dict):
            over = node["sum"].get("over", "")
            for m in re.finditer(r"(\w+)\s+in\s+\w+", over):
                result.add(m.group(1))
            # sum 내부 coeff도 탐색
            if "coeff" in node["sum"]:
                result |= collect_sum_over_vars(node["sum"]["coeff"])
        for key in ["lhs", "rhs", "coeff"]:
            if key in node:
                result |= collect_sum_over_vars(node[key])
        for key in ["add", "subtract", "multiply"]:
            if key in node and isinstance(node[key], list):
                for item in node[key]:
                    result |= collect_sum_over_vars(item)
        return result

    for con in constraints:
        cname = con.get("name", "unknown")
        for_each = con.get("for_each", "")
        con_json = _json.dumps(con, ensure_ascii=False)

        # sum.over에 선언된 변수 수집
        sum_vars = set()
        sum_vars |= collect_sum_over_vars(con.get("lhs", {}))
        sum_vars |= collect_sum_over_vars(con.get("rhs", {}))

        # 제약 JSON에서 사용된 인덱스 변수 탐지 (예: [i,d], [d], [j,d])
        index_refs = set()
        for m in re.findall(r'\[([a-z](?:,[a-z])*)\]', con_json):
            for v in m.split(','):
                index_refs.add(v.strip())

        # for_each에서 이미 선언된 변수
        declared_vars = set()
        for m in re.finditer(r"(\w+)\s+in\s+(\w+)", for_each):
            declared_vars.add(m.group(1))

        # for_each에 추가해야 할 변수 = (인덱스 참조) - (이미 선언) - (sum.over에서 처리)
        missing_vars = index_refs - declared_vars - sum_vars

        for idx_var in sorted(missing_vars):
            set_id = idx_var.upper()
            if set_id in sets_by_id:
                new_loop = f"{idx_var} in {set_id}"
                if for_each:
                    con["for_each"] = f"{new_loop}, {for_each}"
                else:
                    con["for_each"] = new_loop
                for_each = con["for_each"]
                fix_count += 1
                corrections[f"struct_for_each_{cname}_{idx_var}"] = {
                    "type": "for_each_fix",
                    "old": "missing",
                    "new": con["for_each"],
                }
                logger.info(f"Struct fix [{cname}]: added '{new_loop}' to for_each")

        # --- Step 2: source_file 있는 param에 index 누락 -> 추가 ---
        def fix_param_nodes(node, constraint_name, parent_sum_var=None):
            nonlocal fix_count
            if not isinstance(node, dict):
                return
            if "param" in node and isinstance(node["param"], dict):
                p = node["param"]
                has_source = bool(p.get("source_file") or p.get("source_column"))
                has_index = bool(p.get("index"))
                if has_source and not has_index:
                    # sum.over 변수 또는 for_each 첫 번째 변수를 인덱스로 사용
                    if parent_sum_var:
                        idx_var = parent_sum_var
                    else:
                        fe = con.get("for_each", "")
                        loop_vars = re.findall(r"(\w+)\s+in\s+(\w+)", fe)
                        idx_var = loop_vars[0][0] if loop_vars else None
                    if idx_var:
                        p["index"] = f"[{idx_var}]"
                        fix_count += 1
                        pname = p.get("name", "?")
                        corrections[f"struct_index_{constraint_name}_{pname}"] = {
                            "type": "param_index_fix",
                            "old": "no index",
                            "new": p["index"],
                        }
                        logger.info(f"Struct fix [{constraint_name}]: param '{pname}' index set to '{p["index"]}'")

            # sum 노드: over에서 루프변수 추출하여 하위에 전달
            if "sum" in node and isinstance(node["sum"], dict):
                s = node["sum"]
                over = s.get("over", "")
                sv = re.findall(r"(\w+)\s+in\s+\w+", over)
                sum_loop_var = sv[0] if sv else parent_sum_var
                if "coeff" in s:
                    fix_param_nodes(s["coeff"], constraint_name, sum_loop_var)
                # sum 내부 var는 수정 불필요

            for key in ["lhs", "rhs", "coeff"]:
                if key in node:
                    fix_param_nodes(node[key], constraint_name, parent_sum_var)
            for key in ["add", "subtract", "multiply"]:
                if key in node and isinstance(node[key], list):
                    for item in node[key]:
                        fix_param_nodes(item, constraint_name, parent_sum_var)

        fix_param_nodes(con.get("lhs", {}), cname)
        fix_param_nodes(con.get("rhs", {}), cname)

    if set_sizes is None:
        set_sizes = {}

    # ── Set D 크기 교정: duty_count 파라미터로 ──
    try:
        params = {p.get("id") or p.get("name"): p for p in model.get("parameters", [])}
        sets = model.get("sets", [])
        duty_count = None
        # confirmed_problem에서 duty_count 찾기
        for pid, pinfo in params.items():
            if pid in ("duty_count", "총 사업수"):
                val = pinfo.get("value") or pinfo.get("default_value")
                if val is not None:
                    try:
                        duty_count = int(float(val))
                    except (ValueError, TypeError):
                        pass
        # parameters.csv에서 직접 찾기
        if duty_count is None:
            import pandas as _pd
            import os as _os
            _norm_dir = f"uploads/{project_id}/normalized" if project_id else None
            _csv_path = _os.path.join(_norm_dir, "parameters.csv") if _norm_dir and _os.path.exists(_os.path.join(_norm_dir, "parameters.csv")) else None
            if _csv_path:
                _df = _pd.read_csv(_csv_path, dtype=str)
                if "param_name" in _df.columns:
                    for _, _row in _df.iterrows():
                        _pn = str(_row.get("param_name", ""))
                        if _pn in ("총 사업수", "duty_count"):
                            try:
                                duty_count = int(float(_row["value"]))
                            except:
                                pass
        if duty_count and duty_count > 0:
            for s in sets:
                if s.get("id") == "D" and s.get("source_type") == "range":
                    old_size = s.get("size", 0)
                    if old_size > duty_count * 2:
                        s["size"] = duty_count
                        set_sizes["D"] = duty_count
                        fix_count += 1
                        corrections["set_D_size_fix"] = {
                            "type": "set_size_fix",
                            "old": old_size,
                            "new": duty_count,
                        }
                        warnings.append(
                            f"Set D size corrected: {old_size} -> {duty_count} (from duty_count)"
                        )
                        logger.info(f"Struct fix [Set D]: size {old_size} -> {duty_count}")
    except Exception as _e:
        logger.warning(f"Set D size correction failed: {_e}")

    # ── overlap_pairs 주입: 3중 루프 제약에 사전 필터링 적용 ──
    try:
        import json as _json
        import os as _os
        for c in constraints:
            cname = c.get("name", "")
            fe = c.get("for_each", "")
            # 괄호 내부 comma를 무시하는 파싱
            _fe_segments = []
            _buf = ""
            _pdepth = 0
            for _ch in fe + ",":
                if _ch == "(": _pdepth += 1; _buf += _ch
                elif _ch == ")": _pdepth -= 1; _buf += _ch
                elif _ch == "," and _pdepth == 0:
                    if _buf.strip(): _fe_segments.append(_buf.strip())
                    _buf = ""
                else: _buf += _ch
            loop_vars = []
            for _seg in _fe_segments:
                _m = re.match(r"\(?([^)]*?)\)?\s+in\s+(\w+)", _seg.strip())
                if _m:
                    loop_vars.append(_m.group(1).split(",")[0].strip())
            if any("overlap" in _seg for _seg in _fe_segments) or (len(loop_vars) >= 3 and "i" in loop_vars and "j" in loop_vars):
                # uploads/ 하위에서 overlap_pairs.json 탐색
                op_found = False
                _op_dirs = [f"uploads/{project_id}/normalized", f"uploads/{project_id}/phase1"] if project_id else []
                op_path = None
                for _opd in _op_dirs:
                    _opp = _os.path.join(_opd, "overlap_pairs.json")
                    if _os.path.exists(_opp):
                        op_path = _opp
                        break
                if op_path:
                        with open(op_path, "r", encoding="utf-8") as _f:
                            pairs = _json.load(_f)
                        if pairs:
                            c["_overlap_pairs"] = pairs
                            # d 루프만 남기므로 예상 제약 수 = pairs * |D|
                            d_size = 1
                            for lv_name, lv_set in re.findall(r"(\w+)\s+in\s+(\w+)", fe):
                                if lv_name not in ("i", "j"):
                                    d_size *= set_sizes.get(lv_set, 500)
                            old_count = 1
                            for lv_name, lv_set in re.findall(r"(\w+)\s+in\s+(\w+)", fe):
                                old_count *= set_sizes.get(lv_set, 500)
                            new_count = len(pairs) * d_size
                            warnings.append(
                                f"Overlap filter applied to '{cname}': "
                                f"{len(pairs)} pairs ({old_count:,} -> {new_count:,} constraints)"
                            )
                            fix_count += 1
                            logger.info(
                                f"Struct fix [{cname}]: injected {len(pairs)} overlap pairs "
                                f"(reduction: {old_count:,} -> {new_count:,})"
                            )
                            op_found = True
                        break
                if not op_found:
                    logger.warning(f"No overlap_pairs.json found for constraint '{cname}'")
    except Exception as _e:
        logger.warning(f"Overlap pairs injection failed: {_e}")

    if fix_count > 0:
        logger.info(f"Struct validation: {fix_count} structural fixes applied")
    else:
        logger.info("Struct validation: no structural fixes needed")

    return fix_count
