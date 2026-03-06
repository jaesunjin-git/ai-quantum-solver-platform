# ortools_compiler.py의 LP path에서 bound_data 덤프하는 디버그 코드 삽입
# ctx 생성 직후 (line 448 근처)에 param_map, set_map 내용을 파일로 저장

import json

path = 'engine/compiler/ortools_compiler.py'
with open(path, encoding='utf-8') as f:
    content = f.read()

# LP path의 ctx 생성 직후에 디버그 덤프 삽입
marker = "logger.info(f\"BuildContext - vars: {list(var_map.keys())}\")"

debug_code = '''
        # === DEBUG DUMP (임시) ===
        try:
            import json as _djson
            _debug = {
                "param_keys": list(param_map.keys()),
                "param_sample": {k: str(v)[:100] for k, v in list(param_map.items())[:30]},
                "set_keys": list(set_map.keys()),
                "set_sizes": {k: len(v) if isinstance(v, (list, tuple)) else str(v) for k, v in set_map.items()},
                "var_keys": list(var_map.keys()),
                "var_types": {k: ("dict" if isinstance(v, dict) else type(v).__name__) for k, v in var_map.items()},
                "var_sizes": {k: len(v) if isinstance(v, dict) else 1 for k, v in var_map.items()},
            }
            # trip_dep_time, trip_arr_time 상세
            for pname in ["trip_dep_time", "trip_arr_time", "trip_duration", "big_m", "max_driving_minutes", "preparation_minutes"]:
                pval = param_map.get(pname)
                if pval is not None:
                    if isinstance(pval, (list, tuple)):
                        _debug[f"param_{pname}"] = f"array len={len(pval)}, first3={list(pval[:3])}"
                    elif isinstance(pval, dict):
                        _debug[f"param_{pname}"] = f"dict len={len(pval)}, keys={list(pval.keys())[:5]}"
                    else:
                        _debug[f"param_{pname}"] = str(pval)
                else:
                    _debug[f"param_{pname}"] = "NOT FOUND"
            
            # overlap_pairs 상세
            op = set_map.get("overlap_pairs", [])
            _debug["overlap_pairs_size"] = len(op) if isinstance(op, (list, tuple)) else str(op)
            if isinstance(op, (list, tuple)) and len(op) > 0:
                _debug["overlap_pairs_sample"] = [str(x) for x in op[:3]]
            
            with open("uploads/94/debug_bound_data.json", "w", encoding="utf-8") as _df:
                _djson.dump(_debug, _df, ensure_ascii=False, indent=2, default=str)
            logger.info("DEBUG: bound_data dumped to uploads/94/debug_bound_data.json")
        except Exception as _de:
            logger.warning(f"DEBUG dump failed: {_de}")
        # === END DEBUG ==='''

# 두 번째 occurrence를 찾기 (LP path)
occurrences = []
start = 0
while True:
    idx = content.find(marker, start)
    if idx == -1:
        break
    occurrences.append(idx)
    start = idx + 1

if len(occurrences) >= 2:
    # LP path는 두 번째 occurrence
    insert_pos = occurrences[1] + len(marker)
    content = content[:insert_pos] + "\n" + debug_code + "\n" + content[insert_pos:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'[OK] Debug dump inserted after 2nd occurrence (LP path)')
elif len(occurrences) == 1:
    insert_pos = occurrences[0] + len(marker)
    content = content[:insert_pos] + "\n" + debug_code + "\n" + content[insert_pos:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'[OK] Debug dump inserted after 1st occurrence')
else:
    print('[WARN] Marker not found')

import py_compile
py_compile.compile(path, doraise=True)
print('syntax: OK')
