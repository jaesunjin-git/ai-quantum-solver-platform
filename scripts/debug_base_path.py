import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Simulate the exact path calculation from L435-436
sn_file = os.path.abspath("domains/crew/skills/structural_normalization.py")
print(f"sn_file: {sn_file}")

base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(sn_file)))))
print(f"base (4x dirname): {base}")

domains_dir = os.path.join(base, "knowledge", "domains")
print(f"domains_dir: {domains_dir}")
print(f"exists: {os.path.isdir(domains_dir)}")

# What about __file__ inside the actual module?
import domains.crew.skills.structural_normalization as snmod
actual_file = os.path.abspath(snmod.__file__)
print(f"\nActual module __file__: {actual_file}")

base2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    actual_file))))
print(f"base2 (4x dirname): {base2}")

domains_dir2 = os.path.join(base2, "knowledge", "domains")
print(f"domains_dir2: {domains_dir2}")
print(f"exists: {os.path.isdir(domains_dir2)}")

# List what is in domains_dir2
if os.path.isdir(domains_dir2):
    print(f"contents: {os.listdir(domains_dir2)}")
elif os.path.isdir(base2):
    print(f"base2 contents: {os.listdir(base2)}")
