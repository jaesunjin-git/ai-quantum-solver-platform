import sys; sys.path.insert(0, '.')

TARGET = "domains/crew/skills/problem_definition.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

with open(TARGET + ".bak3", "w", encoding="utf-8") as f:
    f.write(content)
print(f"[backup] {TARGET}")

changes = 0

# 패치: _extract_constraint_value에서 특정 타입을 자동 처리
# classification 블록 바로 아래, return unknown_type 바로 위에 삽입

OLD = '''        # classification 등은 Phase 2에서 처리
        elif ctype in ("classification",):
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        return {"status": "unknown_type", "values": {}}'''

NEW = '''        # classification: 주간/야간 구분 등 자동 세팅
        elif ctype in ("classification",):
            auto_values = {}
            params_raw = cdata.get("parameters")
            if isinstance(params_raw, list):
                for p in params_raw:
                    if isinstance(p, str):
                        ref_val = self._lookup_reference_value(p)
                        if ref_val is not None:
                            auto_values[p] = {"value": ref_val, "source": "reference_default", "confidence": 0.6}
                        else:
                            auto_values[p] = {"value": None, "source": "auto_model_variable"}
            if auto_values:
                return {
                    "status": "confirmed",
                    "values": auto_values,
                    "computation_phase": "compile_time",
                }
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        # constant 타입 (big_m 등): 자동 세팅
        elif ctype in ("constant",):
            param_name = cdata.get("parameter")
            if param_name:
                ref_val = self._lookup_reference_value(param_name)
                if param_name == "big_m" and ref_val:
                    # big_m은 데이터 기반 자동 조정: max(기본값, 최대도착시각*2)
                    max_arr = 1440
                    try:
                        import pandas as _pd
                        from pathlib import Path as _Path
                        _base = _Path(__file__).resolve().parents[3] / "uploads"
                        for _pid in sorted(_base.iterdir(), reverse=True):
                            _tt = _pid / "phase1" / "timetable_rows.csv"
                            if _tt.is_file():
                                _df = _pd.read_csv(str(_tt))
                                if "trip_arr_time" in _df.columns:
                                    max_arr = max(max_arr, int(_df["trip_arr_time"].max()))
                                break
                    except Exception:
                        pass
                    auto_val = max(int(ref_val), max_arr * 2)
                    return {
                        "status": "confirmed",
                        "values": {param_name: {"value": auto_val, "source": "auto_computed", "confidence": 1.0}},
                    }
                elif ref_val is not None:
                    return {
                        "status": "confirmed",
                        "values": {param_name: {"value": ref_val, "source": "reference_default", "confidence": 0.9}},
                    }

        return {"status": "unknown_type", "values": {}}'''

if OLD in content:
    content = content.replace(OLD, NEW)
    changes += 1
    print("[patch] auto-set big_m/classification: OK")
else:
    print("[patch] FAILED - block not found")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

import py_compile
py_compile.compile(TARGET, doraise=True)
print(f"[verify] syntax OK")
print(f"\n✅ {changes}개 패치 적용")
