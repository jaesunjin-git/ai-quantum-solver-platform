from core.database import engine
from sqlalchemy import text
import json, pandas as pd

conn = engine.connect()
rows = conn.execute(text('SELECT last_optimization_result FROM core.session_states WHERE project_id = 81')).fetchone()
conn.close()
r = json.loads(rows[0]) if isinstance(rows[0], str) else rows[0]
sol = r['solution']

y_map = json.loads(sol['y']) if isinstance(sol['y'], str) else sol['y']
u_map = json.loads(sol['u']) if isinstance(sol['u'], str) else sol['u']
z_map = json.loads(sol['z']) if isinstance(sol['z'], str) else sol['z']
w_map = json.loads(sol['w']) if isinstance(sol['w'], str) else sol['w']

active = [k for k, v in u_map.items() if v == 1]
print(f'Active duties: {len(active)}')

duty_trips = {}
for k, v in y_map.items():
    if v == 1:
        k_clean = k.replace("(","").replace(")","").replace("'","").replace(" ","")
        parts = k_clean.split(',')
        trip_id = int(parts[0])
        duty_id = parts[1]
        duty_trips.setdefault(duty_id, []).append(trip_id)

trips = pd.read_csv('uploads/81/normalized/trips.csv')
trip_info = trips.set_index('trip_id')

print(f'Duties with trips: {len(duty_trips)}')
print(f'Total trips assigned: {sum(len(t) for t in duty_trips.values())}')

for d in sorted(duty_trips.keys(), key=lambda x: int(x)):
    t_ids = sorted(duty_trips[d])
    zk = f"('{d}',)"
    wk = f"('{d}',)"
    z_val = z_map.get(zk, 0)
    w_val = w_map.get(wk, 0)
    stay = w_val - z_val
    drive = sum(int(trip_info.loc[tid, 'travel_time_min']) for tid in t_ids if tid in trip_info.index)
    first_dep = min(int(trip_info.loc[tid, 'dep_time_min']) for tid in t_ids if tid in trip_info.index)
    last_arr = max(int(trip_info.loc[tid, 'arr_time_min']) for tid in t_ids if tid in trip_info.index)
    print(f'Duty {d:>3}: {len(t_ids):>2} trips, dep={first_dep//60}:{first_dep%60:02d}-arr={last_arr//60}:{last_arr%60:02d}, stay={stay}min, drive={drive}min')
