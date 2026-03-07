import sys, os
with open('domains/crew/skills/math_model.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if any(kw in line for kw in ['param_list_text', 'param_ids', 'param_count']):
        start = max(0, i-3)
        end = min(len(lines), i+8)
        for j in range(start, end):
            print(f'  L{j+1}: {lines[j].rstrip()}')
        print()
