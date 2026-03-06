import sys; sys.path.insert(0, '.')
import yaml

# 1) YAML 프롬프트 확인
with open('prompts/math_model.yaml', encoding='utf-8') as f:
    prompt_cfg = yaml.safe_load(f)

print('=== YAML 구조 ===')
for key in prompt_cfg:
    val = prompt_cfg[key]
    if isinstance(val, str):
        print(f'  {key}: ({len(val)} chars)')
    elif isinstance(val, list):
        print(f'  {key}: [{len(val)} items]')
    elif isinstance(val, dict):
        print(f'  {key}: {list(val.keys())}')
    else:
        print(f'  {key}: {val}')

# 2) soft constraint 관련 섹션 존재 확인
full_text = yaml.dump(prompt_cfg, allow_unicode=True)
for keyword in ['soft', 'penalty', 'slack', 'weight', 'objective_rules']:
    count = full_text.lower().count(keyword)
    print(f'  keyword "{keyword}": {count}회')

# 3) _build_modeling_prompt 함수 확인
with open('engine/math_model_generator.py', encoding='utf-8') as f:
    lines = f.readlines()
func_start = None
func_end = None
for i, line in enumerate(lines):
    if 'def _build_modeling_prompt' in line:
        func_start = i + 1
    if func_start and i > func_start + 5 and line.strip().startswith('def ') and not line.strip().startswith('def _build_modeling_prompt'):
        func_end = i + 1
        break
if func_start:
    print(f'\n=== _build_modeling_prompt: line {func_start} ~ {func_end or "EOF"} ({(func_end or len(lines)) - func_start} lines) ===')
    # soft 관련 코드 확인
    for i in range(func_start - 1, (func_end or len(lines)) - 1):
        if 'soft' in lines[i].lower():
            print(f'  {i+1}: {lines[i].rstrip()}')
