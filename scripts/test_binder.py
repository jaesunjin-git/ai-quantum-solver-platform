import sys, json; sys.path.insert(0, '.')
from engine.compiler.base import DataBinder

# 최신 프로젝트
import glob, os
dirs = sorted(glob.glob('uploads/*/model.json'), key=os.path.getmtime, reverse=True)
if not dirs:
    print("No model.json found")
    exit()

model_path = dirs[0]
pid = model_path.split(os.sep)[1]
print("Project: %s" % pid)

with open(model_path, 'r', encoding='utf-8') as f:
    model = json.load(f)

binder = DataBinder(pid)
bound = binder.bind_all(model)

print("\n=== Sets ===")
for sid, vals in bound["sets"].items():
    sample = str(vals[:5]) if isinstance(vals, list) else str(vals)
    print("  %s: size=%s, sample=%s" % (sid, bound["set_sizes"].get(sid, "?"), sample))

print("\n=== Parameters (scalars) ===")
for pid_name, val in bound["parameters"].items():
    if not isinstance(val, (list, tuple, dict)):
        print("  %s = %s" % (pid_name, val))

print("\n=== Parameters (arrays) ===")
for pid_name, val in bound["parameters"].items():
    if isinstance(val, (list, tuple)):
        print("  %s: len=%d, first3=%s" % (pid_name, len(val), str(val[:3])))
    elif isinstance(val, dict):
        keys = list(val.keys())[:3]
        print("  %s: dict len=%d, sample_keys=%s" % (pid_name, len(val), keys))
