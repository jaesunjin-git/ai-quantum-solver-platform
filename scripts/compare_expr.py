import json, yaml

with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    ydata = yaml.safe_load(f)

ct = ydata.get('constraint_templates', {})

print('=== LLM expression vs YAML expression ===')
match = 0
mismatch = 0
for c in model.get('constraints', []):
    name = c.get('name', '')
    llm_expr = c.get('expression', '').strip()
    yaml_info = ct.get(name, {})
    yaml_expr = yaml_info.get('expression', '').strip() if isinstance(yaml_info, dict) else ''
    
    if not yaml_expr:
        print(f'  [{name}] YAML: (없음)')
        continue
    
    if llm_expr == yaml_expr:
        match += 1
        print(f'  [{name}] MATCH')
    else:
        mismatch += 1
        print(f'  [{name}] MISMATCH')
        print(f'    LLM:  {llm_expr[:100]}')
        print(f'    YAML: {yaml_expr[:100]}')

print(f'\nMatch: {match}, Mismatch: {mismatch}')
