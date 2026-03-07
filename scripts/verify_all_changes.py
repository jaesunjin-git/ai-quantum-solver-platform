with open('domains/crew/skills/problem_definition.py', encoding='utf-8') as f:
    lines = f.readlines()

# 1. _handle_user_response 내 처리 순서 확인
print('=== Handler flow in _handle_user_response ===')
in_handler = False
for i, line in enumerate(lines):
    if '_handle_user_response' in line and 'def ' in line:
        in_handler = True
    if in_handler:
        stripped = line.strip()
        if stripped.startswith('# ──') or stripped.startswith('# 확인') or stripped.startswith('# 수정') or stripped.startswith('# 재시작') or stripped.startswith('# 파라미터') or stripped.startswith('# 기타'):
            print(f'  {i+1}: {stripped[:100]}')
        if '목적함수 변경' in stripped:
            print(f'  {i+1}: {stripped[:100]}')
    if in_handler and 'def get_skill' in line:
        break

# 2. 목적함수 변경 핸들러 존재 확인
print(f'\n=== Objective handler check ===')
content = ''.join(lines)
print(f'목적함수 변경 handler: {"목적함수 변경" in content and "obj_pattern" in content}')
print(f'promote_to_hard logic: {"promote_to_hard" in content}')
print(f'objectives_map: {"objectives_map" in content}')

# 3. _format_proposal 변경 확인
print(f'\n=== _format_proposal check ===')
print(f'is_category_changeable: {content.count("is_category_changeable")} calls')
print(f'변경가능 tag: {"변경가능" in content}')
print(f'카테고리 변경 안내: {"카테고리 변경" in content}')

# 4. 전체 파일 구문 확인
import py_compile
py_compile.compile('domains/crew/skills/problem_definition.py', doraise=True)
print(f'\nSyntax: OK')
print(f'Total lines: {len(lines)}')

# 5. 수정된 파일 목록 정리
print(f'\n=== Modified files summary ===')
import os
files_to_check = [
    'knowledge/domain_loader.py',
    'knowledge/domains/railway/constraints.yaml',
    'knowledge/domains/railway/_index.yaml',
    'domains/crew/skills/problem_definition.py',
]
for fpath in files_to_check:
    if os.path.exists(fpath):
        size = os.path.getsize(fpath)
        with open(fpath, encoding='utf-8') as f:
            lc = len(f.readlines())
        print(f'  {fpath}: {lc} lines, {size} bytes')

# 6. 백업 파일 존재 확인
print(f'\n=== Backup files ===')
backups = [
    'knowledge/domain_loader.py.bak',
    'knowledge/domains/railway/constraints_v2_backup.yaml',
    'domains/crew/skills/problem_definition.py.bak',
]
for bak in backups:
    exists = os.path.exists(bak)
    print(f'  {bak}: {"exists" if exists else "MISSING"}')
