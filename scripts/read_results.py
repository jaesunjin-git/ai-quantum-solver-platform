import json

# 1) KPI
with open('uploads/94/results/kpi_classical_cpu.json', encoding='utf-8') as f:
    kpi = json.load(f)
print('=== KPI ===')
for k, v in kpi.items():
    print(f'  {k}: {v}')

# 2) Interpretation
with open('uploads/94/results/interpretation_classical_cpu.json', encoding='utf-8') as f:
    interp = json.load(f)
print('\n=== INTERPRETATION ===')
for k, v in interp.items():
    if isinstance(v, (str, int, float)):
        print(f'  {k}: {v}')
    elif isinstance(v, list):
        print(f'  {k}: [{len(v)} items]')
    elif isinstance(v, dict):
        print(f'  {k}: {list(v.keys())[:8]}')

# 3) Solution summary
with open('uploads/94/results/solution_classical_cpu.json', encoding='utf-8') as f:
    sol = json.load(f)
print('\n=== SOLUTION SUMMARY ===')
if isinstance(sol, dict):
    y_active = {k: v for k, v in sol.items() if k.startswith('y[') and v == 1.0}
    x_active = {k: v for k, v in sol.items() if k.startswith('x[') and v == 1.0}
    
    slack_sum = {}
    for k, v in sol.items():
        if 'slack' in k and isinstance(v, (int, float)) and v > 0:
            prefix = k.split('[')[0]
            slack_sum[prefix] = slack_sum.get(prefix, 0) + v
    
    print(f'  Active duties (y=1): {len(y_active)}')
    print(f'  Trip assignments (x=1): {len(x_active)}')
    print(f'  Total variables in solution: {len(sol)}')
    if slack_sum:
        print(f'  Slack violations:')
        for sk, sv in sorted(slack_sum.items()):
            print(f'    {sk}: {sv:.1f}')
    print(f'  Objective: 961.0 = {len(y_active)} duties + {961.0 - len(y_active):.1f} slack penalty')
elif isinstance(sol, list):
    print(f'  Solution is a list with {len(sol)} entries')
    if sol:
        print(f'  First entry keys: {list(sol[0].keys()) if isinstance(sol[0], dict) else sol[0]}')
