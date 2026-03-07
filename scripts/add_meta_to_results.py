filepath = "domains/crew/skills/problem_definition.py"
with open(filepath, encoding="utf-8") as f:
    lines = f.readlines()

original_count = len(lines)

# Fix 1: hard_results (line 378-385) - add changeable/fixed from dk._meta
# Find the exact line: "hard_results[cname] = {"
for i, line in enumerate(lines):
    if "hard_results[cname] = {" in line and i > 370 and i < 390:
        # Replace lines 378-385 (index 377-384)
        indent = "            "
        new_block = [
            indent + "# _meta에서 changeable/fixed 정보 추출\n",
            indent + "c_meta = dk.get_constraint_meta(cname) if dk else {}\n",
            indent + "c_fixed = c_meta.get('fixed_category', False)\n",
            indent + "c_changeable = not c_fixed\n",
            indent + "\n",
            indent + "hard_results[cname] = {\n",
            indent + '    "name_ko": cdata.get("name_ko", cname),\n',
            indent + '    "type": ctype,\n',
            indent + '    "description": cdata.get("description", ""),\n',
            indent + '    "status": extraction.get("status", "unknown"),\n',
            indent + '    "values": extraction.get("values", {}),\n',
            indent + '    "computation_phase": extraction.get("computation_phase"),\n',
            indent + '    "fixed": c_fixed,\n',
            indent + '    "changeable": c_changeable,\n',
            indent + "}\n",
        ]
        # Find the closing brace of this dict
        end_idx = i
        for j in range(i+1, min(i+15, len(lines))):
            if lines[j].strip() == "}":
                end_idx = j
                break
        
        lines[i:end_idx+1] = new_block
        print(f"Fixed hard_results at line {i+1}, replaced {end_idx-i+1} lines with {len(new_block)} lines")
        break

# Fix 2: soft_results - find and add changeable/fixed
for i, line in enumerate(lines):
    if "soft_results[cname] = {" in line and i > 390:
        indent = "            "
        new_block = [
            indent + "# _meta에서 changeable/fixed 정보 추출\n",
            indent + "s_meta = dk.get_constraint_meta(cname) if dk else {}\n",
            indent + "s_fixed = s_meta.get('fixed_category', False)\n",
            indent + "s_changeable = not s_fixed\n",
            indent + "\n",
            indent + "soft_results[cname] = {\n",
            indent + '    "name_ko": cdata.get("name_ko", cname),\n',
            indent + '    "type": cdata.get("type", "single_param"),\n',
            indent + '    "description": cdata.get("description", ""),\n',
            indent + '    "weight": default_weight,\n',
            indent + '    "weight_range": weight_range,\n',
            indent + '    "status": "default",\n',
            indent + '    "fixed": s_fixed,\n',
            indent + '    "changeable": s_changeable,\n',
            indent + "}\n",
        ]
        end_idx = i
        for j in range(i+1, min(i+15, len(lines))):
            if lines[j].strip() == "}":
                end_idx = j
                break
        
        lines[i:end_idx+1] = new_block
        print(f"Fixed soft_results at line {i+1}, replaced {end_idx-i+1} lines with {len(new_block)} lines")
        break

with open(filepath, "w", encoding="utf-8") as f:
    f.writelines(lines)

new_count = len(lines)
print(f"\nLines: {original_count} -> {new_count}")

import py_compile
try:
    py_compile.compile(filepath, doraise=True)
    print("Syntax: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")

# Verify
with open(filepath, encoding="utf-8") as f:
    content = f.read()

checks = {
    "hard fixed/changeable": '"fixed": c_fixed' in content and '"changeable": c_changeable' in content,
    "soft fixed/changeable": '"fixed": s_fixed' in content and '"changeable": s_changeable' in content,
    "get_constraint_meta": "get_constraint_meta" in content,
}
print("\n=== Verification ===")
for k, v in checks.items():
    print(f"  {k}: {'OK' if v else 'MISSING'}")
