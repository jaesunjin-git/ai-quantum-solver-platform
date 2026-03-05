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
            for section in ["hard", "soft"]:
                for cid, cdef in (cdata.get(section) or {}).items():
                    if not isinstance(cdef, dict):
                        continue
                    hints_ko = (cdef.get("detection_hints") or {}).get("ko", [])
                    # single_param
                    param_id = cdef.get("parameter")
                    if param_id and hints_ko:
                        for hint in hints_ko:
                            alias_map[hint.strip()] = param_id
                        # Also generate concatenated hint keys
                        if len(hints_ko) >= 2:
                            alias_map["".join(h.strip() for h in hints_ko)] = param_id
                    # compound params
                    params_dict = cdef.get("parameters") or {}
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
    set_ids = set()
    for s in sets:
        sid = s.get("id", "")
        set_ids.add(sid)
        size = _validate_set(s, data_profile, dataframes)
        set_sizes[sid] = size

        if size == 0:
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
        if isinstance(lhs, dict) and "sum" in lhs:
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

            # 모델 파라미터에서 id, name 모두 수집하여 매핑 테이블 구축
            _all_model_params = math_model.get("parameters", [])
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

            # unbound_params 재계산 (검증3 결과 갱신)
            unbound_params = []
            for p in _all_model_params:
                pname = p.get("name", p.get("id", ""))
                sf = p.get("source_file") or ""
                sc = p.get("source_column") or ""
                dv = p.get("default_value", p.get("default"))
                if not sf and not sc and dv is None:
                    unbound_params.append(pname)
            logger.info(f"Direct-bind: {len(unbound_params)} params still unbound: {unbound_params}")


    # --- confirmed_problem 기반 fallback 매핑 ---
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
            _ref_path = _os.path.join(_base, "knowledge", "domains", "railway", "reference_ranges.yaml")
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
        dv = p.get("default_value", p.get("default"))
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
                        return int(df[col].dropna().nunique())

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
        if param_ref and not is_data_col and data_col_names:
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

    avail_list = [c for c in available if c]

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
