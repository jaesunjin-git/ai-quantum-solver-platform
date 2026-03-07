import json, os, sys
sys.path.insert(0, ".")

# Load domain knowledge and simulate constraint extraction
from knowledge.domain_loader import load_domain_knowledge

dk = load_domain_knowledge("railway")
print("=== Hard constraints sample ===")
for name in ["max_wait_time", "max_driving_time", "mandatory_break"]:
    c = dk.hard_constraints.get(name, {})
    print(f"\n{name}:")
    print(f"  keys: {list(c.keys())}")
    vals = c.get("values", {})
    print(f"  values: {vals}")
    params = c.get("parameters", c.get("parameter"))
    print(f"  parameters: {params}")
    ptype = c.get("type")
    print(f"  type: {ptype}")

# Check if there is a current session with proposal data
db_path = None
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "venv")]
    for f in files:
        if f.endswith(".db"):
            db_path = os.path.join(root, f)
            break
    if db_path:
        break

if db_path:
    print(f"\n=== DB found: {db_path} ===")
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT project_id, problem_definition FROM sessions WHERE problem_definition IS NOT NULL LIMIT 1")
    row = cur.fetchone()
    if row:
        pid, pd_json = row
        pd = json.loads(pd_json) if pd_json else {}
        print(f"Project: {pid}")
        hard = pd.get("hard_constraints", {})
        print(f"Hard constraints: {len(hard)}")
        for name in ["max_wait_time", "max_driving_time", "mandatory_break"]:
            c = hard.get(name, {})
            print(f"\n  {name}:")
            print(f"    keys: {list(c.keys())}")
            print(f"    values: {c.get('values', 'NO VALUES KEY')}")
            print(f"    status: {c.get('status')}")
            print(f"    fixed: {c.get('fixed')}")
            print(f"    changeable: {c.get('changeable')}")
        soft = pd.get("soft_constraints", {})
        print(f"\nSoft constraints: {len(soft)}")
        for name in ["avg_driving_time_target", "workload_balance"]:
            c = soft.get(name, {})
            print(f"\n  {name}:")
            print(f"    keys: {list(c.keys())}")
            print(f"    values: {c.get('values', 'NO VALUES KEY')}")
    else:
        print("No session with problem_definition found")
    conn.close()
else:
    print("No DB found")
