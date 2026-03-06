import sys; sys.path.insert(0, '.')
import json
from engine.compiler.struct_builder import BuildContext, build_constraint, eval_node

# model.json 로드
with open('uploads/94/model.json', encoding='utf-8') as f:
    model = json.load(f)

# trips.csv에서 set/param 구성
import pandas as pd
trips = pd.read_csv('uploads/94/normalized/trips.csv')
params = pd.read_csv('uploads/94/normalized/parameters.csv')

# max_driving_time 제약 찾기
for c in model.get('constraints', []):
    if 'max_driving' in c.get('name', ''):
        print('=== max_driving_time constraint ===')
        print(f"  name: {c.get('name')}")
        print(f"  for_each: {c.get('for_each')}")
        print(f"  lhs: {json.dumps(c.get('lhs'), ensure_ascii=False)[:200]}")
        print(f"  operator: {c.get('operator')}")
        print(f"  rhs: {json.dumps(c.get('rhs'), ensure_ascii=False)[:200]}")
        print(f"  expression: {c.get('expression', 'N/A')[:200]}")
        break

# trip_coverage 제약도 확인
for c in model.get('constraints', []):
    if 'coverage' in c.get('name', ''):
        print('\n=== trip_coverage constraint ===')
        print(f"  name: {c.get('name')}")
        print(f"  for_each: {c.get('for_each')}")
        print(f"  lhs: {json.dumps(c.get('lhs'), ensure_ascii=False)[:200]}")
        print(f"  operator: {c.get('operator')}")
        print(f"  rhs: {json.dumps(c.get('rhs'), ensure_ascii=False)[:200]}")
        break

# crew_activation 제약
for c in model.get('constraints', []):
    if 'crew_activation' in c.get('name', ''):
        print('\n=== crew_activation constraint ===')
        print(f"  name: {c.get('name')}")
        print(f"  for_each: {c.get('for_each')}")
        print(f"  lhs: {json.dumps(c.get('lhs'), ensure_ascii=False)[:200]}")
        print(f"  operator: {c.get('operator')}")
        print(f"  rhs: {json.dumps(c.get('rhs'), ensure_ascii=False)[:200]}")
        break
