filepath = "domains/crew/skills/problem_definition.py"
with open(filepath, encoding="utf-8") as f:
    lines = f.readlines()

original_count = len(lines)

# Find soft_results assembly (around line 394-426)
# Need to add value extraction for soft constraints too
# Find the line: "# Soft constraints"
for i, line in enumerate(lines):
    if "# Soft constraints" in line and i > 390:
        # Show current soft constraint loop
        print(f"Found soft constraints section at line {i+1}")
        
        # Find soft_results[cname] = { 
        for j in range(i, min(i+35, len(lines))):
            if "soft_results[cname] = {" in lines[j]:
                # Find the closing brace
                end_j = j
                for k in range(j+1, min(j+20, len(lines))):
                    if lines[k].strip() == "}":
                        end_j = k
                        break
                
                print(f"soft_results assignment at lines {j+1}-{end_j+1}")
                
                # Insert value extraction before soft_results assignment
                indent = "            "
                new_block = [
                    indent + "# ── Phase B: soft도 값 추출 ──\n",
                    indent + "ctype_s = cdata.get('type', 'single_param')\n",
                    indent + "extraction_s = await self._extract_constraint_value(\n",
                    indent + "    model, cname, cdata, ctype_s, phase1_data, state\n",
                    indent + ")\n",
                    indent + "s_values = extraction_s.get('values', {})\n",
                    indent + "s_status = extraction_s.get('status', 'default')\n",
                    "\n",
                    indent + "# _meta에서 changeable/fixed 정보 추출\n",
                    indent + "s_meta = dk.get_constraint_meta(cname) if dk else {}\n",
                    indent + "s_fixed = s_meta.get('fixed_category', False)\n",
                    indent + "s_changeable = not s_fixed\n",
                    "\n",
                    indent + "soft_results[cname] = {\n",
                    indent + "    'name_ko': cdata.get('name_ko', cname),\n",
                    indent + "    'type': ctype_s,\n",
                    indent + "    'description': cdata.get('description', ''),\n",
                    indent + "    'weight': default_weight,\n",
                    indent + "    'weight_range': weight_range,\n",
                    indent + "    'status': s_status,\n",
                    indent + "    'values': s_values,\n",
                    indent + "    'fixed': s_fixed,\n",
                    indent + "    'changeable': s_changeable,\n",
                    indent + "}\n",
                ]
                
                # Find the start of meta extraction (s_meta line)
                meta_start = j
                for m in range(j-5, j):
                    if "s_meta = dk.get_constraint_meta" in lines[m]:
                        meta_start = m
                        break
                
                lines[meta_start:end_j+1] = new_block
                print(f"Replaced lines {meta_start+1}-{end_j+1} with {len(new_block)} lines")
                break
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
    "soft value extraction": "extraction_s = await self._extract_constraint_value" in content,
    "soft values field": "'values': s_values" in content,
    "soft status from extraction": "'status': s_status" in content,
}
print("\n=== Verification ===")
for k, v in checks.items():
    print(f"  {k}: {'OK' if v else 'MISSING'}")
