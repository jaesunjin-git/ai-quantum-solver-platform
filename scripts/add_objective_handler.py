with open('domains/crew/skills/problem_definition.py', encoding='utf-8') as f:
    lines = f.readlines()

# 카테고리 변경 핸들러 앞에 목적함수 변경 핸들러 삽입
# 현재 구조: 확인 → 수정요청 → 재시작 → [여기에 삽입] → 카테고리 변경 → 파라미터 수정 → 기타

# "# ── 제약조건 카테고리 변경" 라인을 찾아서 그 앞에 삽입
insert_idx = None
for i, line in enumerate(lines):
    if '# ── 제약조건 카테고리 변경' in line:
        insert_idx = i
        break

if insert_idx is None:
    print('ERROR: insertion point not found')
    exit(1)

objective_handler = '''        # ── 목적함수 변경 ──
        import re as _re
        obj_pattern = _re.compile(
            r"목적함수[를을]?\s*(.*?)(?:로|으로)\s*변경|"
            r"objective\s+(?:to\s+)?(\w+)",
            _re.IGNORECASE
        )
        obj_match = obj_pattern.search(message)
        if obj_match and state.problem_definition:
            requested = (obj_match.group(1) or obj_match.group(2) or "").strip()

            # dk에서 objectives 로드
            dk = self._load_domain(state)
            import yaml as _yaml
            _constraints_path = "knowledge/domains/railway/constraints.yaml"
            try:
                with open(_constraints_path, encoding="utf-8") as _f:
                    _cdata = _yaml.safe_load(_f)
                objectives_map = _cdata.get("objectives", {})
            except Exception:
                objectives_map = {}

            # 매칭: 이름 또는 description_ko에서 검색
            matched_obj = None
            for oname, odata in objectives_map.items():
                desc_ko = odata.get("description_ko", "")
                if (requested in oname or requested in desc_ko or
                    oname in requested or desc_ko in requested):
                    matched_obj = (oname, odata)
                    break

            if matched_obj:
                oname, odata = matched_obj
                old_obj = state.problem_definition.get("objective", {})
                old_desc = old_obj.get("description", "알 수 없음")

                # 목적함수 업데이트
                state.problem_definition["objective"] = {
                    "type": odata["type"],
                    "target": oname,
                    "description": odata["description"],
                    "description_ko": odata.get("description_ko", odata["description"]),
                    "expression": odata.get("expression", ""),
                    "alternatives": [
                        {"target": k, "description": v.get("description_ko", v["description"])}
                        for k, v in objectives_map.items() if k != oname
                    ],
                }

                # ── 연동: promote_to_hard / recommended_soft 자동 조정 ──
                changes = []
                promote_list = odata.get("promote_to_hard", [])
                for cname in promote_list:
                    if cname in state.problem_definition.get("soft_constraints", {}):
                        moved = state.problem_definition["soft_constraints"].pop(cname)
                        if "hard_constraints" not in state.problem_definition:
                            state.problem_definition["hard_constraints"] = {}
                        state.problem_definition["hard_constraints"][cname] = moved
                        changes.append(f"  - **{cname}**: Soft → Hard (목적함수 연동)")
                        if dk:
                            dk.move_constraint(cname, "hard", force=True)

                save_session_state(project_id, state)

                change_text = ""
                if changes:
                    change_text = "\\n\\n**연동 변경:**\\n" + "\\n".join(changes)

                return {
                    "type": "problem_definition",
                    "text": (
                        f"✅ 목적함수를 변경했습니다.\\n\\n"
                        f"- 이전: {old_desc}\\n"
                        f"- 변경: **{odata.get('description_ko', odata['description'])}**\\n"
                        f"- 수식: {odata.get('expression', '')}"
                        f"{change_text}\\n\\n"
                        f"**확인**을 입력하면 문제 정의가 확정됩니다."
                    ),
                    "data": {
                        "proposal": state.problem_definition,
                        "agent_status": "objective_modified",
                    },
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "추가 수정", "action": "send", "message": "수정"},
                    ],
                }
            else:
                # 매칭 실패: 사용 가능한 목적함수 목록 표시
                obj_list = "\\n".join([
                    f"- {k}: {v.get('description_ko', v['description'])}"
                    for k, v in objectives_map.items()
                ])
                return {
                    "type": "problem_definition",
                    "text": (
                        f"'{requested}'에 해당하는 목적함수를 찾을 수 없습니다.\\n\\n"
                        f"**사용 가능한 목적함수:**\\n{obj_list}\\n\\n"
                        f"위 이름으로 다시 입력해주세요."
                    ),
                    "data": {"agent_status": "objective_change_failed"},
                    "options": [
                        {"label": k, "action": "send", "message": f"목적함수를 {v.get('description_ko', k)}로 변경"}
                        for k, v in list(objectives_map.items())[:4]
                    ],
                }

'''

# 삽입
new_lines = lines[:insert_idx] + [l + '\n' if not l.endswith('\n') else l for l in objective_handler.split('\n')] + lines[insert_idx:]

with open('domains/crew/skills/problem_definition.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

import py_compile
py_compile.compile('domains/crew/skills/problem_definition.py', doraise=True)
print('problem_definition.py objective handler added - syntax OK')
print(f'Lines: {len(lines)} -> {len(new_lines)}')

# 검증
with open('domains/crew/skills/problem_definition.py', encoding='utf-8') as f:
    content = f.read()

print(f'\\nObjective handler present: {"목적함수 변경" in content}')
print(f'Promote_to_hard logic: {"promote_to_hard" in content}')
print(f'Objectives_map loading: {"objectives_map" in content}')

# 흐름 순서 확인
handlers = []
for i, line in enumerate(content.split('\\n')):
    if line.strip().startswith('# ──') and '──' in line:
        handlers.append(f'  {i+1}: {line.strip()}')
    if '_handle_user_response' in line and 'def ' in line:
        handlers.append(f'  {i+1}: {line.strip()}')

print('\\nHandler flow:')
for h in handlers[-10:]:
    print(h)
