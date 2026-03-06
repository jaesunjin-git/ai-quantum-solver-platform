import shutil, py_compile, re

TARGET = 'engine/gates/gate2_model_validate.py'
shutil.copy2(TARGET, TARGET + '.bak_rebuild')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

changes = 0

# ═══════════════════════════════════════════════
# Patch 1: Set 검증에서 fuzzy column match 추가
# ═══════════════════════════════════════════════
old_set_validate = '''    # 4. source_file + source_column — dataframes에서 직접 계산
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
                        return int(df[col].dropna().nunique())'''

new_set_validate = '''    # 4. source_file + source_column — dataframes에서 직접 계산
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
                    return int(df[fuzzy].dropna().nunique())'''

if old_set_validate in src:
    src = src.replace(old_set_validate, new_set_validate)
    changes += 1
    print('[1] Set fuzzy column match: OK')
else:
    print('[1] Set validate 블록 미발견')

# ═══════════════════════════════════════════════
# Patch 2: 바인딩 시작 전에 data column 보호 목록 구축 + confirmed_problem 우선 바인딩
# ═══════════════════════════════════════════════
old_direct_bind_start = '''    # --- 정규화 파라미터에서 직접 바인딩 ---
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
            _bound_count = 0'''

new_direct_bind_start = '''    # ★ 데이터 컬럼 보호: trips.csv 컬럼은 절대 user_input_required 금지
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

                # 유사도 매칭 (토큰 기반)
                if matched_val is None and (pid or pname):
                    best_score = 0
                    for cpk, cpv in _cp_map.items():
                        for candidate in [pid.lower(), pname.lower()]:
                            if not candidate:
                                continue
                            score = _token_similarity(candidate, cpk.lower())
                            if score > best_score and score >= 0.4:
                                best_score = score
                                matched_val = cpv
                                matched_via = f"{cpk} (similarity={score:.2f})"

                if matched_val is not None:
                    try:
                        p["default_value"] = float(matched_val)
                    except (ValueError, TypeError):
                        p["default_value"] = matched_val
                    p["auto_bound"] = True
                    p["auto_bound_source"] = "confirmed_problem"
                    p.pop("user_input_required", None)
                    _cp_bound_phase0 += 1
                    logger.info(f"Phase0-CP-bind: {pid or pname} = {matched_val} (via {matched_via})")
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

            # 모델 파라미터에서 id, name 모두 수집하여 매핑 테이블 구축
            _bound_count = 0'''

if old_direct_bind_start in src:
    src = src.replace(old_direct_bind_start, new_direct_bind_start)
    changes += 1
    print('[2] Phase0 confirmed_problem + column protect: OK')
else:
    print('[2] Direct-bind 시작 블록 미발견')

# ═══════════════════════════════════════════════
# Patch 3: auto_bind 후 protected column 최종 보호
# ═══════════════════════════════════════════════
old_auto_bind_end = '''    # ★ 동적 중복 검출: 1계층(auto_injected) 파라미터 기준'''

new_auto_bind_end = '''    # ★ 최종 보호: 데이터 컬럼 파라미터의 user_input_required 제거
    for p in math_model.get("parameters", []):
        pid = p.get("id", "")
        pname = p.get("name", "")
        if (pid in _protected_columns or pname in _protected_columns) and p.get("user_input_required"):
            p.pop("user_input_required", None)
            if not p.get("source_file"):
                p["source_file"] = "normalized/trips.csv"
                p["source_column"] = pid if pid in _protected_columns else pname
            logger.info(f"Final-protect: {pid or pname} removed from user_input_required")

    # ★ 동적 중복 검출: 1계층(auto_injected) 파라미터 기준'''

if old_auto_bind_end in src:
    src = src.replace(old_auto_bind_end, new_auto_bind_end)
    changes += 1
    print('[3] Final column protection: OK')
else:
    print('[3] 동적 중복 검출 블록 미발견')

# ═══════════════════════════════════════════════
# Patch 4: confirmed_problem fallback 제거 (Phase0에서 이미 처리)
# ═══════════════════════════════════════════════
old_cp_fallback = '''    # --- confirmed_problem 기반 fallback 매핑 ---
    if unbound_params and confirmed_problem:'''
new_cp_fallback = '''    # --- confirmed_problem 기반 fallback 매핑 (Phase0에서 이미 처리, 잔여분만) ---
    if unbound_params and confirmed_problem:'''

if old_cp_fallback in src:
    src = src.replace(old_cp_fallback, new_cp_fallback)
    changes += 1
    print('[4] CP fallback comment update: OK')
else:
    print('[4] CP fallback 블록 미발견')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f'\n총 {changes}개 패치 적용, 문법 검증: OK')
