import csv, os

# Find all normalized parameters.csv files (latest projects)
dirs = sorted(
    [d for d in os.listdir("uploads") if d.isdigit()],
    key=lambda x: int(x), reverse=True
)

for proj_id in dirs[:3]:  # latest 3 projects
    csv_path = f"uploads/{proj_id}/normalized/parameters.csv"
    if os.path.exists(csv_path):
        print(f"{'='*70}")
        print(f"Project {proj_id}: {csv_path}")
        print(f"{'='*70}")
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            rows = list(reader)
        print(f"Headers: {headers}")
        print(f"Total rows: {len(rows)}")
        print()
        # Print ALL rows
        for i, r in enumerate(rows):
            sid = r.get("semantic_id", "")
            pn = r.get("param_name", "")
            val = r.get("value", "")
            unit = r.get("unit", "")
            ctx = r.get("context", "")[:50]
            pt = r.get("param_type", "")
            print(f"  [{i+1:2d}] param_name={pn:30s} semantic_id={sid:30s} value={val:10s} unit={unit:25s} type={pt:12s} context={ctx}")
        print()
