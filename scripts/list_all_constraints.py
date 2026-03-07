import yaml

with open('knowledge/domains/railway/constraints.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

templates = data.get('constraint_templates', {})

print(f'=== constraints.yaml: {len(templates)} templates ===\n')
for name, tdata in templates.items():
    desc = tdata.get('description', '')
    expr = tdata.get('expression_template', tdata.get('expression', ''))
    category = tdata.get('category', tdata.get('priority', 'hard'))
    variables = tdata.get('variables', [])
    parameters = tdata.get('parameters', [])
    print(f'--- {name} ---')
    print(f'  description: {desc}')
    print(f'  category: {category}')
    print(f'  expression: {str(expr)[:120]}')
    print(f'  variables: {variables}')
    print(f'  parameters: {parameters}')
    print()

# templates.yaml도 확인
print('\n=== templates.yaml ===')
with open('knowledge/domains/railway/templates.yaml', encoding='utf-8') as f:
    tdata = yaml.safe_load(f)

for section_name, section in tdata.items():
    if isinstance(section, dict):
        print(f'\n[{section_name}]')
        for name, item in section.items():
            if isinstance(item, dict):
                desc = item.get('description', '')
                expr = item.get('expression', '')
                print(f'  {name}: {desc}')
                print(f'    expr: {str(expr)[:120]}')
                print(f'    variables: {item.get("variables", [])}')
