import json
with open('uploads/94/model.json', encoding='utf-8') as f:
    m = json.load(f)
print('=== SETS ===')
for s in m.get('sets', []):
    print(f"  {s.get('id')}: source_file={s.get('source_file')}, source_col={s.get('source_column')}, source_type={s.get('source_type')}, size={s.get('size')}")
print('\n=== UNBOUND PARAMS ===')
for p in m.get('parameters', []):
    pid = p.get('id', p.get('name',''))
    sf = p.get('source_file','')
    sc = p.get('source_column','')
    dv = p.get('default_value', p.get('default'))
    uir = p.get('user_input_required', False)
    if uir or (not sf and not sc and dv is None):
        print(f"  {pid}: source_file={sf}, source_col={sc}, default={dv}, uir={uir}")
print('\n=== CONFIRMED PROBLEM PARAMS (state) ===')
import sys; sys.path.insert(0,'.')
from domains.crew.session import load_session_state
state = load_session_state('94')
cp = state.confirmed_problem or {}
params = cp.get('parameters', {})
for k, v in list(params.items())[:20]:
    val = v.get('value') if isinstance(v, dict) else v
    print(f"  {k}: {val}")
