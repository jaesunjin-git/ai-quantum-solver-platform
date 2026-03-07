import yaml

path = "knowledge/domains/railway/constraints.yaml"
with open(path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

constraints = data.get("constraints", {})
print(f"Total constraints: {len(constraints)}\n")

for cid, cdef in constraints.items():
    if not isinstance(cdef, dict):
        continue
    cat = cdef.get("category", "?")
    hints = cdef.get("detection_hints", [])
    ctx_must = cdef.get("context_must", [])
    ctx_excl = cdef.get("context_exclude", [])
    param = cdef.get("parameter", "")
    params = cdef.get("parameters", [])
    desc = cdef.get("description", "")[:80]
    
    print(f"[{cat:4s}] {cid}")
    print(f"  desc: {desc}")
    print(f"  hints: {hints}")
    print(f"  param: {param}")
    print(f"  params: {params}")
    if ctx_must:
        print(f"  context_must: {ctx_must}")
    if ctx_excl:
        print(f"  context_exclude: {ctx_excl}")
    print()
