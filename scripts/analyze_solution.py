import json

with open('uploads/94/results/interpretation_classical_cpu.json', encoding='utf-8') as f:
    interp = json.load(f)

# constraint violations
print('=== CONSTRAINT STATUS ===')
for cs in interp.get('constraint_status', []):
    print(f'  {cs}')

# warnings
print('\n=== WARNINGS ===')
for w in interp.get('warnings', []):
    print(f'  {w}')

# duty 1의 상세
duty = interp['duties'][0]
print(f'\n=== DUTY 1 ===')
print(f'  trips: {len(duty.get("trips", []))}')
print(f'  start: {duty.get("start_hhmm")}')
print(f'  end: {duty.get("end_hhmm")}')
print(f'  driving_min: {duty.get("driving_min", "N/A")}')
print(f'  work_min: {duty.get("work_min", "N/A")}')
print(f'  idle_min: {duty.get("idle_min", "N/A")}')

# solution에서 slack 값 확인
with open('uploads/94/results/solution_classical_cpu.json', encoding='utf-8') as f:
    sol = json.load(f)

print(f'\n=== SLACK VALUES ===')
for var_name in ['s_slack', 'h_slack', 'k_slack', 'l_slack', 'o_slack', 'p_slack', 'q_slack', 'r_slack', 't_slack', 'u_slack']:
    data = sol.get(var_name, {})
    nonzero = {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > 0}
    total = sum(nonzero.values()) if nonzero else 0
    print(f'  {var_name}: {len(nonzero)} nonzero, total={total:.1f}')

print(f'\n=== ACTIVE y[j] ===')
y_data = sol.get('y', {})
active = {k: v for k, v in y_data.items() if v == 1.0}
print(f'  Active: {len(active)} duties')
print(f'  Objective: 961.0 = {len(active)} duty + {961.0 - len(active):.1f} slack')
