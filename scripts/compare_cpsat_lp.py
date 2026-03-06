with open('engine/compiler/ortools_compiler.py', encoding='utf-8') as f:
    lines = f.readlines()

# CP-SAT 메서드와 LP 메서드 각각의 시작/끝 라인 찾기
methods = []
current_method = None
indent_level = None

for i, line in enumerate(lines):
    stripped = line.rstrip()
    # 클래스 메서드 시작 감지
    if '    def ' in line and 'self' in line:
        if current_method:
            current_method['end'] = i
            methods.append(current_method)
        current_method = {'name': stripped.strip().split('(')[0].replace('def ', ''), 'start': i+1}
    
if current_method:
    current_method['end'] = len(lines)
    methods.append(current_method)

print('=== All methods in ortools_compiler.py ===')
for m in methods:
    size = m['end'] - m['start']
    print(f"  {m['start']:4d}-{m['end']:4d} ({size:3d} lines): {m['name']}")

# CP-SAT compile과 LP compile 메서드의 주요 기능 비교
print('\n=== CP-SAT path features ===')
cpsat_start = None
cpsat_end = None
lp_start = None
lp_end = None

for m in methods:
    if 'compile_cpsat' in m['name'] or 'compile_cp' in m['name']:
        cpsat_start = m['start'] - 1
        cpsat_end = m['end']
    if 'compile_lp' in m['name'] or 'compile_mip' in m['name']:
        lp_start = m['start'] - 1
        lp_end = m['end']

features_to_check = [
    'expression_parser', 'parse_and_apply_expression',
    'overlap_pairs', 'overlap',
    'soft_constraint', 'soft_slack', '_apply_soft',
    'build_constraint', 'apply_constraint',
    'legacy', '_parse_constraint',
    'BuildContext',
    'param_map', 'set_map',
    'debug_bound_data',
]

if cpsat_start and cpsat_end:
    cpsat_code = ''.join(lines[cpsat_start:cpsat_end])
    print(f'  Range: lines {cpsat_start+1}-{cpsat_end}')
    for feat in features_to_check:
        count = cpsat_code.lower().count(feat.lower())
        print(f'    {feat}: {count} occurrences')

if lp_start and lp_end:
    print(f'\n=== LP path features ===')
    print(f'  Range: lines {lp_start+1}-{lp_end}')
    lp_code = ''.join(lines[lp_start:lp_end])
    for feat in features_to_check:
        count = lp_code.lower().count(feat.lower())
        print(f'    {feat}: {count} occurrences')

# 차이점 요약
if cpsat_start and lp_start:
    print('\n=== DIFF SUMMARY: CP-SAT vs LP ===')
    for feat in features_to_check:
        cp_count = ''.join(lines[cpsat_start:cpsat_end]).lower().count(feat.lower())
        lp_count = ''.join(lines[lp_start:lp_end]).lower().count(feat.lower())
        if cp_count != lp_count:
            status = 'MISMATCH'
        else:
            status = 'OK'
        print(f'  {feat:35s}: CP-SAT={cp_count}, LP={lp_count}  [{status}]')
