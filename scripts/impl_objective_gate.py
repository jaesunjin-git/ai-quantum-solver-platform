import os

filepath = 'domains/crew/skills/problem_definition.py'
with open(filepath, encoding='utf-8') as f:
    lines = f.readlines()

original_count = len(lines)

# ========================================
# 1. SessionState에 objective_changing 추가
# ========================================
session_path = 'domains/crew/session.py'
with open(session_path, encoding='utf-8') as f:
    sess_content = f.read()

if 'objective_changing' not in sess_content:
    sess_content = sess_content.replace(
        '    constraints_confirmed: bool = False',
        '    constraints_confirmed: bool = False\n'
        '    objective_changing: bool = False  # 목적함수 변경 진행 중'
    )
    with open(session_path, 'w', encoding='utf-8') as f:
        f.write(sess_content)
    print('session.py: objective_changing flag added')
else:
    print('session.py: objective_changing already exists')

# ========================================
# 2. 목적함수 변경 핸들러 수정 (lines 1251~1356)
#    - 변경 시 제약조건 재구성 (_determine_constraints_phased 재호출)
#    - 채팅/패널 양쪽 경고 메시지 통일
# ========================================

# 목적함수 변경 핸들러 시작/끝 찾기
obj_handler_start = None
obj_handler_end = None
for i, line in enumerate(lines):
    if '# ── 목적함수 변경 ──' in line:
        obj_handler_start = i
    if obj_handler_start and '# ── 제약조건 카테고리 변경' in line:
        obj_handler_end = i
        break

if obj_handler_start is None or obj_handler_end is None:
    print(f'ERROR: Could not find objective handler boundaries. start={obj_handler_start}, end={obj_handler_end}')
    exit(1)

print(f'Objective handler: lines {obj_handler_start+1}~{obj_handler_end}')

new_obj_handler = '''        # ── 목적함수 변경 ──
        import re as _re
        obj_pattern = _re.compile(
            r"목적함수[를을]?\\s*(.*?)(?:로|으로)\\s*변경|"
            r"objective\\s+(?:to\\s+)?(\\w+)",
            _re.IGNORECASE
        )
        obj_match = obj_pattern.search(message)
        if obj_match and state.problem_definition:
            requested = (obj_match.group(1) or obj_match.group(2) or "").strip()

            # dk에서 objectives 로드
            dk = self._load_domain(state)
            import yaml as _yaml
            domain_name = state.problem_definition.get("domain", "railway")
            _constraints_path = f"knowledge/domains/{domain_name}/constraints.yaml"
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

                # ── 경고 게이트: objective_changing 플래그 확인 ──
                if not getattr(state, 'objective_changing', False):
                    # 첫 번째 요청 → 경고 메시지 + 확인 요청
                    state.objective_changing = True
                    state._pending_objective = {"name": oname, "data": odata}
                    save_session_state(project_id, state)

                    old_obj = state.problem_definition.get("objective", {})
                    old_desc = old_obj.get("description", old_obj.get("description_ko", "현재 목적함수"))
                    new_desc = odata.get("description_ko", odata["description"])

                    promote_info = ""
                    promote_list = odata.get("promote_to_hard", [])
                    if promote_list:
                        promote_info = f"\\n- 자동 Hard 승격 제약: {', '.join(promote_list)}"

                    return {
                        "type": "problem_definition",
                        "text": (
                            f"⚠️ **목적함수 변경 확인**\\n\\n"
                            f"- 현재: {old_desc}\\n"
                            f"- 변경: **{new_desc}**\\n"
                            f"{promote_info}\\n\\n"
                            f"**목적함수를 변경하면 제약조건이 새로 구성됩니다.**\\n"
                            f"현재 수정한 제약조건 편집 내용은 초기화됩니다.\\n\\n"
                            f"계속하시겠습니까?"
                        ),
                        "data": {
                            "agent_status": "objective_change_warning",
                            "pending_objective": oname,
                        },
                        "options": [
                            {"label": "✅ 계속 변경", "action": "send",
                             "message": f"목적함수를 {new_desc}으로 변경"},
                            {"label": "❌ 취소", "action": "send", "message": "취소"},
                        ],
                    }

                # ── 두 번째 요청 (확인됨) → 실제 변경 + 제약조건 재구성 ──
                state.objective_changing = False
                state._pending_objective = None
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

                # ── 제약조건 재구성 ──
                detected_data_types = set(state.problem_definition.get("detected_data_types", []))
                topology = state.problem_definition.get("topology")
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    new_constraints = loop.run_until_complete(
                        self._determine_constraints_phased(
                            None, state, project_id, dk,
                            detected_data_types, topology
                        )
                    ) if not asyncio.get_event_loop().is_running() else (
                        await self._determine_constraints_phased(
                            None, state, project_id, dk,
                            detected_data_types, topology
                        )
                    )
                except Exception:
                    new_constraints = await self._determine_constraints_phased(
                        None, state, project_id, dk,
                        detected_data_types, topology
                    )

                state.problem_definition["hard_constraints"] = new_constraints.get("hard", {})
                state.problem_definition["soft_constraints"] = new_constraints.get("soft", {})

                # promote_to_hard 처리
                changes = []
                promote_list = odata.get("promote_to_hard", [])
                for cname in promote_list:
                    if cname in state.problem_definition.get("soft_constraints", {}):
                        moved = state.problem_definition["soft_constraints"].pop(cname)
                        state.problem_definition["hard_constraints"][cname] = moved
                        changes.append(f"  - **{cname}**: Soft → Hard (목적함수 연동)")
                        if dk:
                            dk.move_constraint(cname, "hard", force=True)

                # 확정 상태 초기화 (재확인 필요)
                state.constraints_confirmed = False
                state.confirmed_constraints = None
                save_session_state(project_id, state)

                hard_count = len(state.problem_definition.get("hard_constraints", {}))
                soft_count = len(state.problem_definition.get("soft_constraints", {}))

                change_text = ""
                if changes:
                    change_text = "\\n\\n**자동 연동 변경:**\\n" + "\\n".join(changes)

                return {
                    "type": "problem_definition",
                    "text": (
                        f"✅ 목적함수를 변경하고 제약조건을 재구성했습니다.\\n\\n"
                        f"- 이전: {old_desc}\\n"
                        f"- 변경: **{odata.get('description_ko', odata['description'])}**\\n"
                        f"- 수식: {odata.get('expression', '')}\\n"
                        f"- 재구성 결과: Hard {hard_count}개, Soft {soft_count}개"
                        f"{change_text}\\n\\n"
                        f"아래에서 제약조건을 확인하고 필요시 수정해주세요."
                    ),
                    "view_mode": "problem_definition",
                    "data": {
                        "proposal": state.problem_definition,
                        "agent_status": "objective_changed_constraints_rebuilt",
                    },
                    "options": [
                        {"label": "✅ 확인", "action": "send", "message": "확인"},
                        {"label": "✏️ 제약조건 수정", "action": "send", "message": "수정"},
                    ],
                }

            else:
                # 매칭 실패
                obj_list = "\\n".join([
                    f"- **{k}**: {v.get('description_ko', v['description'])}"
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
                        {"label": k, "action": "send",
                         "message": f"목적함수를 {v.get('description_ko', k)}로 변경"}
                        for k, v in list(objectives_map.items())[:4]
                    ],
                }

        # ── 취소 처리 (objective_changing 중일 때) ──
        if getattr(state, 'objective_changing', False) and ('취소' in msg_lower or 'cancel' in msg_lower):
            state.objective_changing = False
            state._pending_objective = None
            save_session_state(project_id, state)
            return {
                "type": "problem_definition",
                "text": "목적함수 변경을 취소했습니다. 현재 설정을 유지합니다.",
                "data": {
                    "proposal": state.problem_definition,
                    "agent_status": "objective_change_cancelled",
                },
                "options": [
                    {"label": "✅ 확인", "action": "send", "message": "확인"},
                    {"label": "✏️ 수정", "action": "send", "message": "수정"},
                ],
            }

'''

# 기존 핸들러 교체
new_lines = lines[:obj_handler_start] + [new_obj_handler] + lines[obj_handler_end:]

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

new_count = len(open(filepath, encoding='utf-8').readlines())
print(f'\\nproblem_definition.py: {original_count} -> {new_count} lines')

# 문법 검증
import py_compile
try:
    py_compile.compile(filepath, doraise=True)
    print('problem_definition.py: syntax OK')
except py_compile.PyCompileError as e:
    print(f'SYNTAX ERROR: {e}')

try:
    py_compile.compile(session_path, doraise=True)
    print('session.py: syntax OK')
except py_compile.PyCompileError as e:
    print(f'session.py SYNTAX ERROR: {e}')

# 검증
with open(filepath, encoding='utf-8') as f:
    content = f.read()

checks = {
    'objective_changing flag check': 'objective_changing' in content,
    'warning gate (first request)': 'objective_change_warning' in content,
    'constraints rebuild': '_determine_constraints_phased' in content and 'objective_changed_constraints_rebuilt' in content,
    'promote_to_hard': 'promote_to_hard' in content,
    'cancel handler': 'objective_change_cancelled' in content,
    'constraints_confirmed reset': "state.constraints_confirmed = False" in content,
    'dynamic domain path': 'domain_name = state.problem_definition' in content,
}
print('\\n=== Verification ===')
for label, result in checks.items():
    print(f'  {label}: {"OK" if result else "MISSING"}')
