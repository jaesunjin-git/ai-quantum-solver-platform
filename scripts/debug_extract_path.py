filepath = "domains/crew/skills/problem_definition.py"
with open(filepath, encoding="utf-8") as f:
    lines = f.readlines()

# Show _extract_constraint_value to see what happens with type=None
print("=== _extract_constraint_value full ===")
in_func = False
count = 0
for i, line in enumerate(lines):
    if "def _extract_constraint_value" in line:
        in_func = True
    if in_func:
        print(f"{i+1}: {line.rstrip()[:150]}")
        count += 1
    if in_func and count > 3 and line.strip().startswith("def ") and "_extract_constraint_value" not in line:
        in_func = False
        break

# Also check what ctype is set to before calling _extract_constraint_value
# in _determine_constraints_phased (hard constraints)
print("\n=== ctype assignment in hard constraints loop ===")
for i in range(370, min(395, len(lines))):
    print(f"{i+1}: {lines[i].rstrip()[:150]}")
