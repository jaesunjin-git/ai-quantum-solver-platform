# B안 설계 확인: _build_modeling_prompt 함수의 전체 크기와 프롬프트 조립 범위
with open('engine/math_model_generator.py', encoding='utf-8') as f:
    lines = f.readlines()

# _build_modeling_prompt 함수 범위
start = None
end = None
for i, line in enumerate(lines):
    if 'def _build_modeling_prompt' in line:
        start = i
    elif start and (line.startswith('def ') or line.startswith('async def ') or line.startswith('class ')) and i > start + 5:
        end = i
        break

if start:
    print(f'_build_modeling_prompt: line {start+1} ~ {end+1 if end else "EOF"} ({(end or len(lines)) - start} lines)')

# confirmed_section 조립 부분
for i in range(start or 0, end or len(lines)):
    line = lines[i]
    if 'confirmed_section' in line or 'soft' in line.lower() or 'template' in line.lower():
        print(f'{i+1}: {line.rstrip()}')
