import shutil, re

src = 'domains/crew/skills/problem_definition.py'
shutil.copy2(src, src + '.bak')
print('Backup saved')

with open(src, encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. _format_proposal 수정: 변경 가능 표시 + 안내 문구 추가
# ============================================================

old_hard_section = '''        hard = proposal.get("hard_constraints", {})
        if hard:
            # 상태별 분류
            confirmed = {k: v for k, v in hard.items() if v.get("status") in ("confirmed", "extracted", "auto_computed")}
            partial = {k: v for k, v in hard.items() if v.get("status") == "partial"}
            # auto_model_variable은 입력 필요에서 제외
            needs_input = {}
            for k, v in hard.items():
                if v.get("status") != "user_input_required":
                    continue
                # 하위 값 중 auto_model_variable만 있으면 skip
                vals = v.get("values", {})
                has_real_missing = any(
                    sv.get("source") == "user_input_required" and sv.get("value") is None
                    for sv in vals.values()
                )
                if has_real_missing:
                    needs_input[k] = v
            computed_later = {k: v for k, v in hard.items() if v.get("status") in ("computed_in_phase2",)}

            if confirmed:
                lines.append("**✅ 확인된 제약 (Hard):**")
                for cname, cdata in confirmed.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}")
                lines.append("")

            if partial:
                lines.append("**⚠️ 일부 확인된 제약 (Hard):**")
                for cname, cdata in partial.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}")
                lines.append("")

            if needs_input:
                lines.append("**❓ 입력 필요 (Hard):**")
                for cname, cdata in needs_input.items():
                    name_ko = cdata.get("name_ko", cname)
                    desc = cdata.get("description", "")
                    lines.append(f"- **{name_ko}**: {desc}")
                    for pname, pval in cdata.get("values", {}).items():
                        ref = pval.get("reference_range")
                        ref_default = pval.get("reference_default")
                        if ref and ref_default is not None:
                            lines.append(f"  - {pname} = ??? (참고 범위: {ref}, 기본값: {ref_default})")
                        elif ref:
                            lines.append(f"  - {pname} = ??? (참고 범위: {ref})")
                        elif ref_default is not None:
                            lines.append(f"  - {pname} = ??? (기본값: {ref_default})")
                        else:
                            lines.append(f"  - {pname} = ???")
                lines.append("")

            if computed_later:
                lines.append("**🔄 자동 계산 예정 (Phase 2):**")
                for cname, cdata in computed_later.items():
                    name_ko = cdata.get("name_ko", cname)
                    lines.append(f"- **{name_ko}**: 데이터 정규화 후 자동 계산")
                lines.append("")

        soft = proposal.get("soft_constraints", {})
        if soft:
            lines.append("**선택 제약 (Soft):**")
            for cname, cdata in soft.items():
                name_ko = cdata.get("name_ko", cname)
                weight = cdata.get("weight", 0)
                lines.append(f"- **{name_ko}**: {cdata.get('description','')} (가중치: {weight})")
            lines.append("")'''

new_hard_section = '''        # ── 도메인 지식에서 변경 가능 여부 조회 ──
        dk_ref = dk  # _format_proposal의 dk 인자 활용

        hard = proposal.get("hard_constraints", {})
        if hard:
            # 상태별 분류
            confirmed = {k: v for k, v in hard.items() if v.get("status") in ("confirmed", "extracted", "auto_computed")}
            partial = {k: v for k, v in hard.items() if v.get("status") == "partial"}
            needs_input = {}
            for k, v in hard.items():
                if v.get("status") != "user_input_required":
                    continue
                vals = v.get("values", {})
                has_real_missing = any(
                    sv.get("source") == "user_input_required" and sv.get("value") is None
                    for sv in vals.values()
                )
                if has_real_missing:
                    needs_input[k] = v
            computed_later = {k: v for k, v in hard.items() if v.get("status") in ("computed_in_phase2",)}

            if confirmed:
                lines.append("**✅ 확인된 제약 (Hard):**")
                for cname, cdata in confirmed.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    changeable = dk_ref.is_category_changeable(cname) if dk_ref else False
                    tag = " [변경가능]" if changeable else ""
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}{tag}")
                lines.append("")

            if partial:
                lines.append("**⚠️ 일부 확인된 제약 (Hard):**")
                for cname, cdata in partial.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    changeable = dk_ref.is_category_changeable(cname) if dk_ref else False
                    tag = " [변경가능]" if changeable else ""
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}{tag}")
                lines.append("")

            if needs_input:
                lines.append("**❓ 입력 필요 (Hard):**")
                for cname, cdata in needs_input.items():
                    name_ko = cdata.get("name_ko", cname)
                    desc = cdata.get("description", "")
                    lines.append(f"- **{name_ko}**: {desc}")
                    for pname, pval in cdata.get("values", {}).items():
                        ref = pval.get("reference_range")
                        ref_default = pval.get("reference_default")
                        if ref and ref_default is not None:
                            lines.append(f"  - {pname} = ??? (참고 범위: {ref}, 기본값: {ref_default})")
                        elif ref:
                            lines.append(f"  - {pname} = ??? (참고 범위: {ref})")
                        elif ref_default is not None:
                            lines.append(f"  - {pname} = ??? (기본값: {ref_default})")
                        else:
                            lines.append(f"  - {pname} = ???")
                lines.append("")

            if computed_later:
                lines.append("**🔄 자동 계산 예정 (Phase 2):**")
                for cname, cdata in computed_later.items():
                    name_ko = cdata.get("name_ko", cname)
                    lines.append(f"- **{name_ko}**: 데이터 정규화 후 자동 계산")
                lines.append("")

        soft = proposal.get("soft_constraints", {})
        if soft:
            lines.append("**선택 제약 (Soft):**")
            for cname, cdata in soft.items():
                name_ko = cdata.get("name_ko", cname)
                weight = cdata.get("weight", 0)
                changeable = dk_ref.is_category_changeable(cname) if dk_ref else False
                tag = " [변경가능]" if changeable else ""
                lines.append(f"- **{name_ko}**: {cdata.get('description','')} (가중치: {weight}){tag}")
            lines.append("")

        # ── 카테고리 변경 안내 ──
        if hard or soft:
            lines.append("---")
            lines.append("💡 **제약조건 카테고리 변경 안내:**")
            lines.append("- [변경가능] 표시된 제약은 Hard↔Soft 변경이 가능합니다.")
            lines.append("- 변경 예시: mandatory_break soft로 변경 또는 max_total_stay_time hard로 변경")
            lines.append("- 표시 없는 제약은 구조적 필수 제약이므로 변경할 수 없습니다.")
            lines.append("")'''

if old_hard_section in content:
    content = content.replace(old_hard_section, new_hard_section)
    print('1. _format_proposal updated')
else:
    print('1. WARNING: _format_proposal pattern not found, trying line-based')

# ============================================================
# 2. _handle_user_response 수정: 카테고리 변경 핸들러 추가
# ============================================================

# 파라미터 수정 섹션 앞에 카테고리 변경 핸들러 삽입
old_param_section = '''        # 파라미터 수정 (key = value 패턴)
        param_pattern = re.compile(r"(\\w+)\\s*[=:：]\\s*(\\d+(?:\\.\\d+)?)")
        matches = param_pattern.findall(message)'''

new_category_and_param = '''        # ── 제약조건 카테고리 변경 (hard↔soft) ──
        category_pattern = re.compile(
            r"(\\w+)\\s+(?:를\\s*|을\\s*)?(?:로\\s*)?(hard|soft)(?:로)?(?:\\s*변경|\\s*전환|\\s*바꿔|\\s*바꾸)",
            re.IGNORECASE
        )
        cat_match = category_pattern.search(message)
        if not cat_match:
            # 영어 패턴: "change max_total_stay_time to hard"
            cat_pattern_en = re.compile(
                r"(?:change|move|switch|set)\\s+(\\w+)\\s+(?:to\\s+)?(hard|soft)",
                re.IGNORECASE
            )
            cat_match = cat_pattern_en.search(message)

        if cat_match and state.problem_definition:
            cname = cat_match.group(1)
            to_cat = cat_match.group(2).lower()

            # dk 로드
            dk = self._load_domain(state)

            # pending_category_change가 있으면 사용자가 경고에 확인한 것
            pending = getattr(state, '_pending_category_change', None)
            if pending and pending.get("constraint") == cname and pending.get("to") == to_cat:
                # 사용자가 이전 경고에 대해 다시 같은 명령 → force
                force = True
                state._pending_category_change = None
            else:
                force = False

            result = dk.move_constraint(cname, to_cat, force=force)

            if result["success"]:
                # problem_definition의 hard/soft 딕셔너리도 업데이트
                from_cat = "soft" if to_cat == "hard" else "hard"
                from_key = f"{from_cat}_constraints"
                to_key = f"{to_cat}_constraints"

                if cname in state.problem_definition.get(from_key, {}):
                    moved_data = state.problem_definition[from_key].pop(cname)
                    if to_key not in state.problem_definition:
                        state.problem_definition[to_key] = {}
                    state.problem_definition[to_key][cname] = moved_data

                save_session_state(project_id, state)

                name_ko = dk.get_constraint(cname)
                if name_ko and isinstance(name_ko, dict):
                    name_ko = name_ko.get("description", cname)
                else:
                    name_ko = cname

                return {
                    "type": "problem_definition",
                    "text": (
                        f"✅ **{name_ko}** 제약을 **{to_cat.upper()}**로 변경했습니다.\\n\\n"
                        f"**확인**을 입력하면 문제 정의가 확정됩니다."
                    ),
                    "data": {
                        "proposal": state.problem_definition,
                        "agent_status": "category_modified",
                    },
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "추가 수정", "action": "send", "message": "수정"},
                    ],
                }

            elif result["needs_confirm"]:
                # 경고 표시, 다시 같은 명령을 보내면 force 적용
                state._pending_category_change = {"constraint": cname, "to": to_cat}
                save_session_state(project_id, state)

                return {
                    "type": "problem_definition",
                    "text": (
                        f"{result['warning']}\\n\\n"
                        f"변경을 확정하려면 동일한 명령을 다시 입력하세요:\\n"
                        f"{cname} {to_cat}로 변경"
                    ),
                    "data": {"agent_status": "category_change_pending"},
                    "options": [
                        {"label": f"{cname} {to_cat}로 변경", "action": "send", "message": f"{cname} {to_cat}로 변경"},
                        {"label": "취소", "action": "send", "message": "취소"},
                    ],
                }

            else:
                return {
                    "type": "problem_definition",
                    "text": f"❌ {result['warning']}",
                    "data": {"agent_status": "category_change_failed"},
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                    ],
                }

        # 파라미터 수정 (key = value 패턴)
        param_pattern = re.compile(r"(\\w+)\\s*[=:：]\\s*(\\d+(?:\\.\\d+)?)")
        matches = param_pattern.findall(message)'''

if old_param_section in content:
    content = content.replace(old_param_section, new_category_and_param)
    print('2. _handle_user_response category handler added')
else:
    print('2. WARNING: param section pattern not found')

# ============================================================
# 3. 수정 안내 메시지 업데이트
# ============================================================

old_modify_msg = '''                    "수정할 항목을 알려주세요. 예시:\\n\\n"
                    "- 목적함수를 [목적함수명]으로 변경\\n"
                    "- [파라미터명] = [값]\\n"
                    "- [제약조건명] 제거\\n"'''

new_modify_msg = '''                    "수정할 항목을 알려주세요. 예시:\\n\\n"
                    "- 목적함수를 [목적함수명]으로 변경\\n"
                    "- [파라미터명] = [값]\\n"
                    "- [제약조건명] 제거\\n"
                    "- [제약조건명] soft로 변경 (Hard→Soft)\\n"
                    "- [제약조건명] hard로 변경 (Soft→Hard)\\n"'''

content = content.replace(old_modify_msg, new_modify_msg)
print('3. Modify message updated')

# ============================================================
# 4. 기타 안내 메시지 업데이트
# ============================================================

old_fallback = '''                "**확인**, **수정**, 또는 **다시 분석**을 입력해주세요.\\n"
                "파라미터 수정은 파라미터명 = 값 형식으로 입력할 수 있습니다."'''

new_fallback = '''                "**확인**, **수정**, 또는 **다시 분석**을 입력해주세요.\\n"
                "파라미터 수정: 파라미터명 = 값\\n"
                "카테고리 변경: 제약조건명 hard/soft로 변경"'''

content = content.replace(old_fallback, new_fallback)
print('4. Fallback message updated')

with open(src, 'w', encoding='utf-8') as f:
    f.write(content)

import py_compile
py_compile.compile(src, doraise=True)
print('\\nproblem_definition.py updated and syntax OK')

# 변경 줄 수 확인
with open(src, encoding='utf-8') as f:
    new_lines = f.readlines()
with open(src + '.bak', encoding='utf-8') as f:
    old_lines = f.readlines()
print(f'Lines: {len(old_lines)} -> {len(new_lines)} (+{len(new_lines)-len(old_lines)})')
