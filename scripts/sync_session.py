import sys; sys.path.insert(0, '.')
import json
from domains.crew.session import load_session_state, save_session_state

# 1) 수정된 model.json 로드
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

# 2) session state 업데이트
state = load_session_state('94')
old_vars = len(state.math_model.get('variables', [])) if state.math_model else 0
old_cons = len(state.math_model.get('constraints', [])) if state.math_model else 0

state.math_model = model
state.math_model_confirmed = False
save_session_state('94', state)

# 3) 확인
new_vars = len(model.get('variables', []))
new_cons = len(model.get('constraints', []))
soft = len([c for c in model.get('constraints', []) if c.get('priority') == 'soft'])

print(f'Session state updated:')
print(f'  Variables: {old_vars} -> {new_vars} (slack: {len([v for v in model["variables"] if "slack" in v.get("id","")])})')
print(f'  Constraints: {old_cons} -> {new_cons} (hard={new_cons-soft}, soft={soft})')
print(f'  math_model_confirmed: False (확정 대기)')
print(f'  Objective: {model["objective"]["expression"][:80]}...')
