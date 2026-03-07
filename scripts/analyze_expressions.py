import yaml

with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

ct = data.get('constraint_templates', {})
hard = data.get('hard', {})

print('=== ALL EXPRESSION PATTERNS ===')
patterns = set()
for src in [ct, hard]:
    for k, v in src.items():
        if isinstance(v, dict):
            expr = v.get('expression', '')
        elif isinstance(v, str):
            expr = v
        else:
            continue
        if not expr or 'SKIP' in expr or 'CONSTANT' in expr:
            continue
        # 패턴 추출: 변수/파라미터명을 일반화
        print(f'\n  {k}:')
        print(f'    expr: {expr}')
        # 사용되는 연산 종류
        ops = []
        if 'sum(' in expr: ops.append('sum')
        if '*' in expr: ops.append('multiply')
        if '-' in expr: ops.append('subtract')
        if '+' in expr: ops.append('add')
        for op in ['<=', '>=', '==']:
            if op in expr:
                ops.append(f'compare:{op}')
        print(f'    ops: {ops}')
