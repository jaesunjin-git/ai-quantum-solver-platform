import json, psycopg2

conn = psycopg2.connect(
    host='localhost', port=5432, dbname='quantum_db',
    user='postgres', password='password1234'
)
cur = conn.cursor()
cur.execute("SELECT math_model FROM core.session_states WHERE project_id = 94")
model = json.loads(cur.fetchone()[0])

# 기존 14개 + linking 유지, overlap을 단순화된 형태로 추가
no_overlap = {
    "name": "no_overlap",
    "category": "hard",
    "description": "시간이 겹치는 운행은 같은 승무원에 배정 불가",
    "for_each": "(i1,i2) in overlap_pairs, j in J",
    "expression": "x[i1,j] + x[i2,j] <= 1",
    "lhs": {
        "add": [
            {"var": "x", "index": "[i1,j]"},
            {"var": "x", "index": "[i2,j]"}
        ]
    },
    "operator": "<=",
    "rhs": {"value": 1}
}

model['constraints'].append(no_overlap)
print(f'Total constraints: {len(model["constraints"])}')
print(f'Added: no_overlap (x[i1,j] + x[i2,j] <= 1)')

new_json = json.dumps(model, ensure_ascii=False)
cur.execute("UPDATE core.session_states SET math_model = %s WHERE project_id = 94", (new_json,))
conn.commit()
cur.close()
conn.close()
print('Restart server -> run solver')
