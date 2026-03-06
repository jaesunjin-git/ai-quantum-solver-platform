with open('domains/crew/skills/math_model.py', encoding='utf-8') as f:
    content = f.read()

overwrite_code = '''
            # ── YAML expression 자동 덮어쓰기 ──
            # LLM이 생성한 expression 대신 constraints.yaml의 검증된 수식 사용
            try:
                from engine.math_model_generator import _load_domain_yaml
                _dy = _load_domain_yaml(state.domain or 'railway')
                _ct = _dy.get('constraint_templates', {})
                _aux = _dy.get('auxiliary_variables', {})
                _efix = 0
                _removes = set()
                for _con in model.get('constraints', []):
                    _cn = _con.get('name', '')
                    _yct = _ct.get(_cn, {})
                    if isinstance(_yct, dict):
                        _ye = _yct.get('expression', '').strip()
                        _yf = _yct.get('for_each', '').strip()
                        if _ye:
                            if _ye.startswith('SKIP') or _ye.startswith('CONSTANT'):
                                _removes.add(_cn)
                                continue
                            if _con.get('expression', '') != _ye:
                                _con['expression'] = _ye
                                _efix += 1
                            if _yf and _con.get('for_each', '') != _yf:
                                _con['for_each'] = _yf
                if _removes:
                    model['constraints'] = [
                        c for c in model['constraints']
                        if c.get('name') not in _removes
                    ]
                    logger.info(f'Removed non-constraints: {_removes}')
                _vids = {v['id'] for v in model.get('variables', [])}
                for _aid, _ainfo in _aux.items():
                    if _aid not in _vids and isinstance(_ainfo, dict):
                        model.setdefault('variables', []).append({
                            'id': _aid,
                            'type': _ainfo.get('type', 'continuous'),
                            'indices': _ainfo.get('indices', []),
                            'description': _ainfo.get('description', '')
                        })
                        logger.info(f'Auto-added auxiliary variable: {_aid}')
                if _efix > 0:
                    logger.info(f'YAML expression overwrite: {_efix} constraints corrected')
            except Exception as _oe:
                logger.warning(f'YAML expression overwrite failed: {_oe}')

'''

# line 228: gate2_result = run_gate2(...)
marker = '            gate2_result = run_gate2(model, data_profile=data_profile, dataframes=binder._dataframes)'

if 'YAML expression 자동 덮어쓰기' not in content:
    idx = content.find(marker)
    if idx > 0:
        content = content[:idx] + overwrite_code + content[idx:]
        with open('domains/crew/skills/math_model.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print('[OK] YAML expression overwrite inserted before Gate2')
    else:
        print('[WARN] marker not found')
else:
    print('[SKIP] already exists')

import py_compile
py_compile.compile('domains/crew/skills/math_model.py', doraise=True)
print('syntax: OK')
