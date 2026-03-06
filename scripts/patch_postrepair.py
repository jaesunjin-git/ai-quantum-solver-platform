import shutil, py_compile

TARGET = 'domains/crew/skills/math_model.py'
shutil.copy2(TARGET, TARGET + '.bak_postfix')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

changes = 0

# ═══════════════════════════════════════════════
# Patch: repair 병합 후 후처리 삽입
# ═══════════════════════════════════════════════
# repair 완료 후 "수정된 모델로 Gate2 재검증" 주석 앞에 후처리 추가

old_after_repair = '''                            # 수정된 모델로 Gate2 재검증 (continue로 루프 반복)
                            # model은 이미 수정되었으므로 다음 attempt에서 재검증됨
                            # 단, generate_math_model을 다시 호출하지 않도록 플래그 설정'''

new_after_repair = '''                            # ★ 후처리 1: repair가 추가한 제약에서 참조하는 미등록 변수 자동 등록
                            existing_var_ids = {v.get("id") for v in model.get("variables", [])}
                            for con in model.get("constraints", []):
                                for side in ["lhs", "rhs"]:
                                    node = con.get(side, {})
                                    if isinstance(node, dict) and "var" in node:
                                        var_ref = node["var"]
                                        vname = var_ref.get("name", "") if isinstance(var_ref, dict) else str(var_ref)
                                        if vname and vname not in existing_var_ids:
                                            # 인덱스 추출
                                            vidx = var_ref.get("index", "") if isinstance(var_ref, dict) else ""
                                            idx_list = [c.strip().upper() for c in vidx.strip("[]").split(",") if c.strip()]
                                            new_var = {
                                                "id": vname,
                                                "name": vname,
                                                "type": "binary",
                                                "indices": idx_list,
                                                "description": f"Auto-registered from repair ({con.get('name','')})",
                                            }
                                            model["variables"].append(new_var)
                                            existing_var_ids.add(vname)
                                            logger.info(f"  Auto-registered variable: {vname} indices={idx_list}")

                            # ★ 후처리 2: 양쪽에 의사결정 변수 없는 제약 자동 제거
                            def _has_decision_var(node, var_ids):
                                if not isinstance(node, dict):
                                    return False
                                if "var" in node:
                                    vr = node["var"]
                                    vn = vr.get("name","") if isinstance(vr, dict) else str(vr)
                                    return vn in var_ids
                                if "sum" in node and isinstance(node["sum"], dict):
                                    if "var" in node["sum"]:
                                        return True
                                for k in ["add","subtract","multiply"]:
                                    if k in node:
                                        items = node[k] if isinstance(node[k], list) else [node[k]]
                                        if any(_has_decision_var(it, var_ids) for it in items):
                                            return True
                                return False

                            all_var_ids = {v.get("id") for v in model.get("variables", [])}
                            before_count = len(model["constraints"])
                            model["constraints"] = [
                                c for c in model["constraints"]
                                if _has_decision_var(c.get("lhs",{}), all_var_ids)
                                or _has_decision_var(c.get("rhs",{}), all_var_ids)
                            ]
                            removed_count = before_count - len(model["constraints"])
                            if removed_count > 0:
                                logger.info(f"  Removed {removed_count} constraints with no decision variables")

                            # ★ 후처리 3: Set J 크기 조정 (승무원 → 듀티 상한)
                            trip_count = 0
                            for s in model.get("sets", []):
                                if s.get("id") == "I":
                                    trip_count = s.get("size", 0)
                                    if not trip_count and s.get("source_file"):
                                        try:
                                            import pandas as _pd
                                            _tf = s["source_file"]
                                            for _dk, _dv in dataframes.items():
                                                if _tf in _dk:
                                                    trip_count = len(_dv)
                                                    break
                                        except Exception:
                                            pass
                            if trip_count > 0:
                                duty_estimate = max(trip_count // 4, 20)  # 경험적: 운행수/4
                                for s in model.get("sets", []):
                                    if s.get("id") == "J" and s.get("source_type") == "range":
                                        old_size = s.get("size", 0)
                                        if old_size > duty_estimate * 2:
                                            s["size"] = duty_estimate
                                            logger.info(f"  Set J size adjusted: {old_size} -> {duty_estimate} (trips={trip_count})")

                            # 수정된 모델로 Gate2 재검증 (continue로 루프 반복)
                            # model은 이미 수정되었으므로 다음 attempt에서 재검증됨
                            # 단, generate_math_model을 다시 호출하지 않도록 플래그 설정'''

if old_after_repair in src:
    src = src.replace(old_after_repair, new_after_repair)
    changes += 1
    print('[1] Post-repair 후처리 삽입: OK')
else:
    print('[1] repair 후 주석 미발견')

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(src)

py_compile.compile(TARGET, doraise=True)
print(f'\n총 {changes}개 패치, 문법 검증: OK')
