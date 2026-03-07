import json

# 결과 파싱
y_data = json.loads('{"(\"3001\", \"80\")": 1, "(\"3002\", \"80\")": 1}')  # 샘플

# 직접 데이터 사용
sol_y = {"('3001', '80')": 1, "('3002', '80')": 1, "('3003', '49')": 1}  # 위 데이터 전체

# 파일에서 읽기
from core.database import engine
from sqlalchemy import text
import json, pandas as pd

conn = engine.connect()
rows = conn.execute(text('SELECT last_optimization_result FROM core.session_states WHERE project_id = 81')).fetchone()
conn.close()
r = json.loads(rows[0]) if isinstance(rows[0], str) else rows[0]
sol = r['solution']

# y 파싱 - 듀티별 운행 목록
y_map = json.loads(sol['y']) if isinstance(sol['y'], str) else sol['y']
u_map = json.loads(sol['u']) if isinstance(sol['u'], str) else sol['u']
z_map = json.loads(sol['z']) if isinstance(sol['z'], str) else sol['z']
w_map = json.loads(sol['w']) if isinstance(sol['w'], str) else sol['w']

# 활성 듀티
active = [k for k, v in u_map.items() if v == 1]
print(f'Active duties: {len(active)}')

# 듀티별 운행 배정
duty_trips = {}
for k, v in y_map.items():
    if v == 1:
        k_clean = k.strip("()' ").replace("'","").replace(" ","")
        parts = k_clean.split(',')
        trip_id = parts[0]
        duty_id = parts[1]
        duty_trips.setdefault(duty_id, []).append(int(trip_id))

# trips.csv 로드
trips = pd.read_csv('uploads/81/normalized/trips.csv')
trip_info = trips.set_index('trip_id')

print(f'\nDuties with trips: {len(duty_trips)}')
print(f'Total trips assigned: {sum(len(t) for t in duty_trips.values())}')

# 각 듀티 상세
for d in sorted(duty_trips.keys(), key=lambda x: int(x)):
    t_ids = sorted(duty_trips[d])
    z_val = z_map.get(f"('{d}',)", 0)
    w_val = w_map.get(f"('{d}',)", 0)
    stay = w_val - z_val
    drive = sum(trip_info.loc[tid, 'travel_time_min'] for tid in t_ids if tid in trip_info.index)
    print(f'Duty {d:>3}: {len(t_ids):>2} trips, z={z_val}({z_val//60}:{z_val%60:02d}), w={w_val}({w_val//60}:{w_val%60:02d}), stay={stay}min, drive={drive}min')
