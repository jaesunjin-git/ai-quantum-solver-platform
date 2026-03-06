import json

with open('uploads/94/model.json', encoding='utf-8') as f:
    m = json.load(f)

print('=== HARD CONSTRAINTS ===')
for c in m.get('constraints', []):
    if c.get('priority', 'hard') == 'hard':
        name = c.get('name', '')
        op = c.get('operator', '')
        for_each = c.get('for_each', '')
        expr = c.get('expression', '')[:100]
        rhs = c.get('rhs', {})
        
        # rhs 값 추출
        if isinstance(rhs, dict):
            if 'value' in rhs:
                rhs_val = rhs['value']
            elif 'param' in rhs:
                rhs_val = rhs['param'] if isinstance(rhs['param'], str) else rhs['param'].get('name', '')
            elif 'var' in rhs:
                rhs_val = f"var:{rhs['var'].get('name', '') if isinstance(rhs['var'], dict) else rhs['var']}"
            else:
                rhs_val = str(rhs)[:50]
        else:
            rhs_val = rhs
            
        print(f'  {name}: {op} {rhs_val} (for_each: {for_each})')
        print(f'    expr: {expr}')
