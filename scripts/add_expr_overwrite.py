with open('engine/math_model_generator.py', encoding='utf-8') as f:
    content = f.read()

# Gate2 직전, model JSON 저장 직전에 expression 덮어쓰기 로직 삽입
# 현재 line 252 부근: "# [DEBUG] 모델 JSON 저장 (디버깅용)"
# 그 직전에 삽입

overwrite_code = '''
            # ── YAML expression 자동 덮어쓰기 ──
            # LLM이 생성한 expression 대신 constraints.yaml의 검증된 수식을 사용
            try:
                _domain_yaml_for_expr = _load_domain_yaml(domain)
                _ct_for_expr = _domain_yaml_for_expr.get("constraint_templates", {})
                _expr_fix_count = 0
                _remove_names = set()

                for _con in model.get("constraints", []):
                    _cname = _con.get("name", "")
                    _yaml_ct = _ct_for_expr.get(_cname, {})
                    if isinstance(_yaml_ct, dict):
                        _yaml_expr = _yaml_ct.get("expression", "").strip()
                        _yaml_fe = _yaml_ct.get("for_each", "").strip()
                        if _yaml_expr:
                            # SKIP/CONSTANT 표시된 것은 제거 대상
                            if _yaml_expr.startswith("SKIP") or _yaml_expr.startswith("CONSTANT"):
                                _remove_names.add(_cname)
                                continue
                            if _con.get("expression", "") != _yaml_expr:
                                _con["expression"] = _yaml_expr
                                _expr_fix_count += 1
                            if _yaml_fe and _con.get("for_each", "") != _yaml_fe:
                                _con["for_each"] = _yaml_fe

                # SKIP/CONSTANT 제약 제거
                if _remove_names:
                    model["constraints"] = [
                        c for c in model["constraints"]
                        if c.get("name") not in _remove_names
                    ]
                    logger.info(f"Removed non-constraint entries: {_remove_names}")

                # auxiliary_variables 확인 및 추가
                _aux_vars = _domain_yaml_for_expr.get("auxiliary_variables", {})
                _existing_var_ids = {v["id"] for v in model.get("variables", [])}
                for _avid, _avinfo in _aux_vars.items():
                    if _avid not in _existing_var_ids and isinstance(_avinfo, dict):
                        model.setdefault("variables", []).append({
                            "id": _avid,
                            "type": _avinfo.get("type", "continuous"),
                            "indices": _avinfo.get("indices", []),
                            "description": _avinfo.get("description", "")
                        })
                        logger.info(f"Auto-added auxiliary variable: {_avid}")

                if _expr_fix_count > 0:
                    logger.info(f"YAML expression overwrite: {_expr_fix_count} constraints corrected")
            except Exception as _oe:
                logger.warning(f"YAML expression overwrite failed: {_oe}")
'''

# 삽입 위치: "# [DEBUG] 모델 JSON 저장" 직전
marker = '            # [DEBUG] 모델 JSON 저장'
if 'YAML expression 자동 덮어쓰기' not in content:
    idx = content.find(marker)
    if idx > 0:
        content = content[:idx] + overwrite_code + '\n' + content[idx:]
        with open('engine/math_model_generator.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print('[OK] YAML expression overwrite logic inserted')
    else:
        print('[WARN] marker not found')
else:
    print('[SKIP] already exists')

import py_compile
py_compile.compile('engine/math_model_generator.py', doraise=True)
print('syntax: OK')
