import sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. Check what YAML looks like now
v3_path = "knowledge/domains/railway/constraints.yaml"
with open(v3_path, "r", encoding="utf-8") as f:
    v3 = yaml.safe_load(f) or {}

print("=== YAML top keys ===")
print(f"  {list(v3.keys())}")
print(f"  has 'hard': {'hard' in v3}")
print(f"  has 'soft': {'soft' in v3}")
print(f"  has 'constraints': {'constraints' in v3}")
c = v3.get("constraints", {})
first_cid = list(c.keys())[0]
print(f"  First constraint: {first_cid}")
print(f"  Its hints: {c[first_cid].get('detection_hints', 'MISSING')}")
print(f"  Its category: {c[first_cid].get('category', 'MISSING')}")

# 2. Check _load_rules code - does it have v3 block?
sn_path = "domains/crew/skills/structural_normalization.py"
with open(sn_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"\n=== _load_rules: v3 block present? ===")
has_v3 = False
for i, line in enumerate(lines):
    if "v3 format" in line or "constraints" in line and "unified" in line:
        has_v3 = True
        print(f"  L{i+1}: {line.rstrip()}")
    if "skip if already processed" in line.lower() or "Skip if already" in line:
        print(f"  L{i+1}: {line.rstrip()}")
        # Show next 2 lines
        for j in range(1, 3):
            if i+j < len(lines):
                print(f"  L{i+1+j}: {lines[i+j].rstrip()}")

if not has_v3:
    print("  v3 block NOT FOUND in code!")

# 3. Manually simulate _load_rules
print(f"\n=== Manual simulation of _load_rules ===")
domains_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath("domains/crew/skills/structural_normalization.py"))))),
    "knowledge", "domains")
# Use direct path instead
domains_dir = "knowledge/domains"
print(f"  domains_dir: {domains_dir}, exists: {os.path.isdir(domains_dir)}")

rules = []
for dname in os.listdir(domains_dir):
    cpath = os.path.join(domains_dir, dname, "constraints.yaml")
    if not os.path.isfile(cpath):
        print(f"  {dname}: no constraints.yaml")
        continue
    with open(cpath, "r", encoding="utf-8") as f:
        cdata = yaml.safe_load(f) or {}
    
    # v2 check
    v2_count = 0
    for section in ["hard", "soft"]:
        for cid, cdef in (cdata.get(section) or {}).items():
            if isinstance(cdef, dict) and cdef.get("detection_hints"):
                v2_count += 1
    print(f"  {dname}: v2 rules={v2_count}, has_constraints={'constraints' in cdata}")
    
    # v3 check
    if "constraints" in cdata:
        skip = bool(cdata.get("hard") or cdata.get("soft"))
        print(f"  {dname}: v3 skip={skip} (hard={bool(cdata.get('hard'))}, soft={bool(cdata.get('soft'))})")
        if not skip:
            for cid, cdef in cdata["constraints"].items():
                if not isinstance(cdef, dict):
                    continue
                hints = cdef.get("detection_hints", [])
                if hints:
                    params = cdef.get("parameters", [])
                    param = cdef.get("parameter", "")
                    if param:
                        rules.append(param)
                    if isinstance(params, list):
                        rules.extend([p for p in params if isinstance(p, str)])
                    elif isinstance(params, dict):
                        rules.extend(params.keys())

print(f"\n  Simulated v3 rules: {len(rules)}")
for r in rules[:10]:
    print(f"    {r}")
