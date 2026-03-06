with open('engine/math_model_generator.py', encoding='utf-8') as f:
    content = f.read()

# _build_modeling_prompt 마지막 return 직전에 프롬프트를 파일로 저장하는 디버그 코드 삽입
debug_code = '''
    # === DEBUG: 프롬프트 저장 ===
    import os as _dbg_os
    _dbg_dir = _dbg_os.path.join('uploads', '94')
    _dbg_os.makedirs(_dbg_dir, exist_ok=True)
    with open(_dbg_os.path.join(_dbg_dir, 'debug_prompt.txt'), 'w', encoding='utf-8') as _dbg_f:
        _dbg_f.write(prompt)
    logger.info(f"DEBUG: prompt saved to uploads/94/debug_prompt.txt ({len(prompt)} chars)")
    # === END DEBUG ===
'''

# return prompt 바로 앞에 삽입
marker = '    return prompt'
# 마지막 occurrence 찾기
idx = content.rfind(marker)
if idx > 0 and 'debug_prompt.txt' not in content:
    content_new = content[:idx] + debug_code + '\n' + content[idx:]
    with open('engine/math_model_generator.py', 'w', encoding='utf-8') as f:
        f.write(content_new)
    print('[OK] Debug prompt save inserted')
else:
    if 'debug_prompt.txt' in content:
        print('[SKIP] Debug already exists')
    else:
        print(f'[WARN] marker not found')

import py_compile
py_compile.compile('engine/math_model_generator.py', doraise=True)
print('syntax: OK')
