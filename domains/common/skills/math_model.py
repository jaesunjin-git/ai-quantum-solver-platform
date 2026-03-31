from __future__ import annotations
"""
domains/common/skills/math_model.py
─────────────────────────────────
수학 모델 생성 및 관리 스킬.

skill_math_model: LLM을 사용하여 분석 데이터로부터 수학적 최적화 모델 생성.
skill_show_math_model: 기존 수학 모델을 다시 표시.
handle_math_model_confirm: 수학 모델 확정 처리 및 다음 단계 안내.

리팩토링 Step 4c에서 agent.py CrewAgent로부터 추출됨.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.config import settings
from core.platform.session import SessionState, CrewSession, save_session_state
from core.platform.utils import (
    build_facts_summary, build_next_options, clean_report,
    domain_display, error_response, extract_text_from_llm
)
from domains.crew.skills.general import skill_general

from engine.math_model_generator import generate_math_model, repair_constraints, summarize_model
from engine.gates.gate2_model_validate import run as run_gate2, to_text_summary as gate2_to_text
from core.version.model_service import create_model_version

logger = logging.getLogger(__name__)


def _build_auto_inform_text(model: dict) -> str:
    informs = model.get("metadata", {}).get("auto_param_inform", [])
    if not informs:
        return ""
    visible = [i for i in informs if i["param"] != "big_m"]
    if not visible:
        return ""
    lines = ["\nℹ️ **시스템 자동 설정 파라미터:**\n"]
    for info in visible:
        lines.append(f"- {info['reason']}\n")
    lines.append("\n위 값을 변경하시려면 말씀해 주세요.\n\n")
    return "".join(lines)



async def skill_math_model(model, session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    state = session.state

    if not state.analysis_completed:
        return {
            "type": "warning",
            "text": "⚠️ 아직 데이터 분석이 완료되지 않았습니다. 먼저 '데이터 분석'을 진행해 주세요.",
            "data": None,
            "options": [{"label": "📊 분석 시작", "action": "send", "message": "데이터 분석 시작해줘"}],
        }


    # ★ 사용자 파라미터 입력 처리
    if state.pending_param_inputs and state.math_model:
        import re as _re
        # 사용자 메시지에서 key=value 쌍 추출
        pairs = _re.findall(r"([\w]+)\s*[=:：]\s*([\d.]+)", message)
        if pairs:
            applied = []
            param_map = {
                p.get("id", p.get("name", "")): p
                for p in state.math_model.get("parameters", [])
            }
            for key, val in pairs:
                if key in param_map:
                    param_map[key]["default_value"] = float(val)
                    param_map[key]["value"] = float(val)  # value도 동기화
                    param_map[key].pop("user_input_required", None)
                    applied.append(f"{key}={val}")

            # 남은 pending 업데이트
            still_pending = [
                pid for pid in state.pending_param_inputs
                if pid in param_map and param_map[pid].get("user_input_required")
            ]

            if still_pending:
                state.pending_param_inputs = still_pending
                save_session_state(project_id, state)
                param_lines = [f"  - **{p}**" for p in still_pending]
                return {
                    "type": "param_input",
                    "text": (
                        f"✅ {len(applied)}개 파라미터 적용 완료: {', '.join(applied)}\n\n"
                        f"아직 **{len(still_pending)}개** 파라미터가 남았습니다:\n"
                        + "\n".join(param_lines)
                        + "\n\n값을 입력해 주세요."
                    ),
                    "data": {
                        "view_mode": "param_input",
                        "pending_params": still_pending,
                        "math_model": state.math_model,
                    },
                    "options": [],
                }
            else:
                # 모든 파라미터 입력 완료
                state.pending_param_inputs = None
                state.math_model_confirmed = False
                save_session_state(project_id, state)

                summary = summarize_model(state.math_model)
                return {
                    "type": "analysis",
                    "text": (
                        f"✅ 모든 파라미터 입력 완료! ({', '.join(applied)})\n\n"
                        f"📐 **수학 모델이 완성되었습니다.**\n\n"
                        f"오른쪽 패널에서 확인 후 '모델 확정'을 눌러주세요."
                    ),
                    "data": {
                        "view_mode": "math_model",
                        "math_model": state.math_model,
                        "math_model_summary": summary,
                    },
                    "options": [
                        {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                        {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    ],
                }

    # ── 목적함수 변경 → 문제 정의 단계 (confirmed 여부 무관) ──
    is_objective_change = (
        ("목적" in message and any(kw in message for kw in ["변경", "바꿔", "바꾸"]))
        or (params and params.get("user_objective"))
    )
    if is_objective_change and state.math_model:
        state.reset_from_math_model()
        state.problem_defined = False
        save_session_state(project_id, state)
        from domains.crew.skills.problem_definition import skill_problem_definition
        return await skill_problem_definition(model, session, project_id, message, {})

    # ── 확정/확인 요청 → handle_math_model_confirm으로 라우팅 ──
    if state.math_model and any(kw in message for kw in ["확정", "확인", "맞아", "좋아", "ok"]):
        return await handle_math_model_confirm(model, session, project_id, message)

    if state.math_model and not state.math_model_confirmed:
        summary = summarize_model(state.math_model)
        return {
            "type": "analysis",
            "text": "📐 **이전에 생성된 수학 모델입니다.**\n\n확인 후 다음 단계를 진행해 주세요.",
            "data": {
                "view_mode": "math_model",
                "math_model": state.math_model,
                "math_model_summary": summary,
            },
            "options": [
                {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                {"label": "✏️ 목적함수 변경", "action": "send", "message": "목적함수 변경"},
                {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
            ],
        }

    # 이미 확정된 모델이 있는 경우
    if state.math_model and state.math_model_confirmed:

        # 재생성/수정 요청이면 초기화 후 아래 생성 로직으로
        is_regenerate = any(kw in message for kw in ["다시", "재생성", "regenerate", "바꿔", "수정"])
        is_param_regen = params and params.get("regenerate")
        if is_regenerate or is_param_regen:
            state.reset_from_math_model()
            save_session_state(project_id, state)
        else:
            summary = summarize_model(state.math_model)
            return {
                "type": "analysis",
                "text": "📐 **이미 확정된 수학 모델입니다.**\n\n재생성하려면 '모델 재생성'을 눌러주세요.",
                "data": {
                    "view_mode": "math_model",
                    "math_model": state.math_model,
                    "math_model_summary": summary,
                },
                "options": [
                    {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                ],
            }

    try:
        # 사용자가 목적함수를 지정했는지 확인
        # 1순위: LLM이 추출한 구조화된 파라미터
        user_objective = params.get("user_objective") if params else None

        # 2순위: 메시지에서 키워드 기반 추출 (fallback)
        if not user_objective:
            objective_keywords = ["최소화", "최대화", "minimize", "maximize", "공평", "비용", "균등", "운행시간", "인건비"]
            if any(kw in message for kw in objective_keywords):
                user_objective = message

        csv_summary = state.csv_summary or ""
        analysis_report = state.last_analysis_report or ""
        domain = state.detected_domain or "generic"


        # ═══════════════════════════════════════════════════════════
        # ★ 경로 분기: 도메인 템플릿 기반 조립 vs LLM 생성
        # ═══════════════════════════════════════════════════════════
        from engine.template_model_builder import (
            load_domain_template, classify_route, build_model_from_template
        )

        _template = load_domain_template(domain)
        _confirmed_cc = state.confirmed_constraints or {}
        _use_template = False

        if _template and _confirmed_cc:
            _route, _matched, _unmatched = classify_route(_confirmed_cc, _template)
            logger.info(f"Model generation route: {_route} (matched={len(_matched)}, unmatched={len(_unmatched)})")

            if _route == "A":
                logger.info("Route A: building model from template (no LLM)")
                _tmpl_result = build_model_from_template(
                    template=_template,
                    confirmed_constraints=_confirmed_cc,
                    confirmed_problem=state.confirmed_problem or {},
                    phase1_summary=state.phase1_summary or {},
                    project_id=project_id,
                )
                if _tmpl_result["success"]:
                    _use_template = True
                else:
                    logger.warning(f"Template build failed: {_tmpl_result.get('error')} -> LLM fallback")

            elif _route == "B":
                logger.info(f"Route B: unmatched {_unmatched} -> LLM fallback")

        if _use_template:
            model = _tmpl_result["model"]
            validation = _tmpl_result["validation"]

            # DataBinder + Gate2
            from engine.compiler.base import DataBinder
            binder = DataBinder(project_id)
            binder.load_files()
            from engine.gates.gate1_data_profile import run as run_gate1
            data_profile = run_gate1(binder._dataframes)
            gate2_result = run_gate2(model, data_profile=data_profile, dataframes=binder._dataframes,
                                     confirmed_problem=state.confirmed_problem or {})

            # 템플릿 경로: overlap_pairs 크기 에러는 컴파일에 영향 없으므로 경고로 다운그레이드
            _real_errors = [
                e for e in gate2_result.get("errors", [])
                if "overlap_pairs" not in e
            ]
            _downgraded = [
                e for e in gate2_result.get("errors", [])
                if "overlap_pairs" in e
            ]
            if _downgraded:
                gate2_result["warnings"].extend([f"(다운그레이드) {e}" for e in _downgraded])
                gate2_result["errors"] = _real_errors
                gate2_result["valid"] = len(_real_errors) == 0
                logger.info(f"Gate2: downgraded {len(_downgraded)} overlap_pairs errors to warnings")

            logger.info(
                f"Gate2 (template): valid={gate2_result['valid']}, "
                f"errors={len(gate2_result['errors'])}, warnings={len(gate2_result['warnings'])}"
            )

            # Gate2 corrections 적용
            if gate2_result.get("corrections"):
                model_str = json.dumps(model, ensure_ascii=False)
                for ckey, cval in gate2_result["corrections"].items():
                    if isinstance(cval, dict) and cval.get("type") == "column_name_fix":
                        old_t = '"' + cval["old"] + '"'
                        new_t = '"' + cval["new"] + '"'
                        model_str = model_str.replace(old_t, new_t)
                try:
                    model = json.loads(model_str)
                except json.JSONDecodeError:
                    pass

            # 모델 JSON 저장
            _model_dir = os.path.join("uploads", str(project_id))
            os.makedirs(_model_dir, exist_ok=True)
            with open(os.path.join(_model_dir, "model.json"), "w", encoding="utf-8") as _mf:
                json.dump(model, _mf, ensure_ascii=False, indent=2)

            # user_input_required 체크
            need_input_params = [
                p.get("id", p.get("name", ""))
                for p in model.get("parameters", [])
                if p.get("user_input_required")
                and not p.get("source_file")
                and not p.get("source_column")
            ]
            if need_input_params:
                state.math_model = model
                state.pending_param_inputs = need_input_params
                save_session_state(project_id, state)
                param_lines = [f"  - **{pn}**" for pn in need_input_params]
                return {
                    "type": "param_input",
                    "text": (
                        f"📐 수학 모델이 생성되었으나, **{len(need_input_params)}개 파라미터**의 값을 "
                        f"데이터에서 자동으로 찾을 수 없습니다.\n\n"
                        + "\n".join(param_lines)
                    ),
                    "data": {"view_mode": "param_input", "pending_params": need_input_params, "math_model": model},
                    "options": [{"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"}],
                }

            # Gate2 경고 병합
            if gate2_result.get("warnings"):
                validation.setdefault("warnings", []).extend(gate2_result["warnings"])
            if gate2_result.get("errors"):
                validation.setdefault("errors", []).extend(gate2_result["errors"])

            # 세션 저장 + 결과 반환
            state.math_model = model
            state.math_model_confirmed = False
            state.last_executed_skill = "MathModelSkill"
            save_session_state(project_id, state)

            summary = summarize_model(model)
            meta = model.get("metadata", {})

            return {
                "type": "analysis",
                "text": (
                    f"📐 **수학 모델이 생성되었습니다.** (템플릿 기반)\n\n"
                    f"추정 변수 수: **{meta.get('estimated_variable_count', '?')}개**\n"
                    f"추정 제약 수: **{meta.get('estimated_constraint_count', '?')}개**\n\n"
                    + _build_auto_inform_text(model)
                    + "오른쪽 패널에서 상세 모델을 확인하고, 맞으면 '모델 확정'을 눌러주세요."
                ),
                "data": {
                    "view_mode": "math_model",
                    "math_model": model,
                    "math_model_summary": summary,
                },
                "options": [
                    {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "✏️ 목적함수 변경", "action": "send", "message": "목적함수를 변경하고 싶어요"},
                ],
            }

        # ★ 재시도 루프: Gate 2 검증 실패 시 최대 3회 재생성
        MAX_RETRIES = 3
        retry_feedback = ""
        final_model = None
        final_validation = None
        final_gate2 = None

        # DataBinder 초기화 (data_guide 및 Gate2에서 사용)
        from engine.compiler.base import DataBinder
        binder = DataBinder(project_id)
        binder.load_files()

        for attempt in range(1, MAX_RETRIES + 1):
            logger.info(f"Math model generation attempt {attempt}/{MAX_RETRIES}")

            # repair 완료된 모델이면 LLM 재호출 없이 Gate2만 재검증
            if retry_feedback == "__REPAIR_DONE__":
                logger.info(f"Repair done — re-validating without LLM (attempt {attempt})")
                result = {
                    "success": True,
                    "model": model,
                    "validation": {"valid": True, "errors": [], "warnings": []},
                    "error": None,
                }
            else:
                # ── normalized/parameters.csv에서 semantic_id 기반 파라미터 주입 ──
                _cp = state.confirmed_problem or {}
                if not _cp.get("parameters"):
                    import csv as _csv_mod
                    import re as _re_mod
                    _norm_csv = os.path.join("uploads", str(project_id), "normalized", "parameters.csv")
                    if os.path.exists(_norm_csv):
                        _injected = {}
                        try:
                            with open(_norm_csv, "r", encoding="utf-8") as _f:
                                for _row in _csv_mod.DictReader(_f):
                                    _sid = (_row.get("semantic_id") or "").strip()
                                    _pn = (_row.get("param_name") or "").strip()
                                    if _sid and _sid != _pn:
                                        _base = _re_mod.sub(r"_(2|3|4|5|avg|min|max)$", "", _sid)
                                        if _base not in _injected:
                                            _injected[_sid] = {"value": _row.get("value", ""), "unit": _row.get("unit", "minutes")}
                            if _injected:
                                if not _cp:
                                    _cp = {}
                                _cp["parameters"] = _injected
                                logger.info(f"Param inject: {len(_injected)} params from normalized/parameters.csv")
                        except Exception as _inj_e:
                            logger.warning(f"Param inject failed: {_inj_e}")

                result = await generate_math_model(
                    csv_summary=csv_summary,
                    analysis_report=analysis_report,
                    domain=domain,
                    user_objective=user_objective,
                    data_facts=state.data_facts,
                    retry_feedback=retry_feedback,
                    dataframes=binder._dataframes if binder else None,
                    confirmed_problem=_cp if _cp else state.confirmed_problem,
                )

            if not result["success"]:
                error_msg = result.get("error", "알 수 없는 오류")
                warnings = result.get("validation", {}).get("warnings", []) if result.get("validation") else []
                warning_text = "\n".join([f"  ⚠️ {w}" for w in warnings]) if warnings else ""
                if attempt < MAX_RETRIES:
                    retry_feedback = f"모델 생성 실패: {error_msg}"
                    continue
                return error_response(
                    f"수학 모델 생성에 실패했습니다 ({MAX_RETRIES}회 시도): {error_msg}\n{warning_text}",
                    "수학 모델 생성해줘"
                )

            model = result["model"]
            validation = result["validation"]

            # ★ Gate 2: 모델 유효성 검증
            from engine.gates.gate1_data_profile import run as run_gate1
            data_profile = run_gate1(binder._dataframes)

            # ── YAML expression 자동 덮어쓰기 ──
            # LLM이 생성한 expression 대신 constraints.yaml의 검증된 수식 사용
            try:
                from engine.math_model_generator import _load_domain_yaml
                _dy = _load_domain_yaml(state.detected_domain or settings.DEFAULT_DOMAIN)
                _ct = _dy.get('constraint_templates', {})
                _aux = _dy.get('auxiliary_variables', {})
                _efix = 0
                _removes = set()
                for _con in model.get('constraints', []):
                    _cn = _con.get('name', '')
                    _yct = _ct.get(_cn, {})
                    if isinstance(_yct, dict):
                        _ye = _yct.get('expression', '').strip()
                        _yf = _yct.get('for_each', '').strip()
                        if _ye:
                            if _ye.startswith('SKIP') or _ye.startswith('CONSTANT'):
                                _removes.add(_cn)
                                continue
                            if _con.get('expression', '') != _ye:
                                _con['expression'] = _ye
                                _efix += 1
                            if _yf and _con.get('for_each', '') != _yf:
                                _con['for_each'] = _yf
                if _removes:
                    model['constraints'] = [
                        c for c in model['constraints']
                        if c.get('name') not in _removes
                    ]
                    logger.info(f'Removed non-constraints: {_removes}')
                _vids = {v['id'] for v in model.get('variables', [])}
                for _aid, _ainfo in _aux.items():
                    if _aid not in _vids and isinstance(_ainfo, dict):
                        model.setdefault('variables', []).append({
                            'id': _aid,
                            'type': _ainfo.get('type', 'continuous'),
                            'indices': _ainfo.get('indices', []),
                            'description': _ainfo.get('description', '')
                        })
                        logger.info(f'Auto-added auxiliary variable: {_aid}')
                if _efix > 0:
                    logger.info(f'YAML expression overwrite: {_efix} constraints corrected')
            except Exception as _oe:
                logger.warning(f'YAML expression overwrite failed: {_oe}')

            gate2_result = run_gate2(model, data_profile=data_profile, dataframes=binder._dataframes,
                                     confirmed_problem=state.confirmed_problem or {})

            # ★ Gate2 corrections를 모델에 실제 적용 (column_name_fix)
            if gate2_result.get("corrections"):
                import json as _json
                model_str = json.dumps(model, ensure_ascii=False)
                applied_count = 0
                for ckey, cval in gate2_result["corrections"].items():
                    if isinstance(cval, dict) and cval.get("type") == "column_name_fix":
                        old_name = cval["old"]
                        new_name = cval["new"]
                        # JSON 문자열에서 정확한 값 치환 ("old_name" -> "new_name")
                        old_token = f'\"' + old_name + f'\"'
                        new_token = f'\"' + new_name + f'\"'
                        if old_token in model_str:
                            model_str = model_str.replace(old_token, new_token)
                            applied_count += 1
                            logger.info(f"Applied correction: {old_name} -> {new_name}")
                        # expression 내부 치환은 단어 경계 기준으로 수행
                        import re as _re
                        model_str = _re.sub(r'(?<![a-zA-Z_])' + _re.escape(old_name) + r'(?![a-zA-Z_])', new_name, model_str)
                if applied_count > 0:
                    try:
                        model = json.loads(model_str)
                        logger.info(f"Gate2 corrections applied: {applied_count} column name fixes")
                    except json.JSONDecodeError as _je:
                        logger.warning(f"Gate2 correction broke JSON at pos {_je.pos}, keeping original model")

            # [DEBUG] 모델 JSON 저장 (디버깅용)
            import os as _os
            _model_dir = _os.path.join('uploads', str(project_id))
            _os.makedirs(_model_dir, exist_ok=True)
            _model_path = _os.path.join(_model_dir, 'model.json')
            with open(_model_path, 'w', encoding='utf-8') as _mf:
                json.dump(model, _mf, ensure_ascii=False, indent=2)
            logger.info(f'Model JSON saved to {_model_path}')

            logger.info(
                f"Gate2 (attempt {attempt}): valid={gate2_result['valid']}, "
                f"errors={len(gate2_result['errors'])}, "
                f"warnings={len(gate2_result['warnings'])}, "
                f"actual_vars={gate2_result['actual_variable_count']}"
            )

            # 재생성 필요 여부 판단
            needs_retry = False
            retry_reasons = []

            # 조건 1: 치명적 오류
            if gate2_result.get("errors"):
                needs_retry = True
                retry_reasons.extend(gate2_result["errors"])

            # 조건 2: 경고 과다 (20개 이상)
            if len(gate2_result.get("warnings", [])) >= 50:
                needs_retry = True
                retry_reasons.append(
                    f"검증 경고 {len(gate2_result['warnings'])}개 — 모델 품질 부족"
                )

            # 조건 3: 변수 수 비정상 (1000 미만이면 set 매핑 오류 가능성)
            actual_vars = gate2_result.get("actual_variable_count", 0)
            if actual_vars < 1000:
                needs_retry = True
                retry_reasons.append(
                    f"변수 수 {actual_vars}개로 비정상적으로 적음 — set 매핑 오류 가능성"
                )

            # 조건 4: set 크기 0
            for sid, size in gate2_result.get("actual_set_sizes", {}).items():
                if size == 0:
                    needs_retry = True
                    retry_reasons.append(f"Set '{sid}' 크기가 0 — 데이터 매핑 실패")

            # 구조 에러 repair는 마지막 attempt에서도 실행
            has_structural = needs_retry and any(
                ("binary" in r.lower() or "이진" in r or "0/1" in r
                 or "변수가 없" in r or "양쪽 모두 변수" in r)
                for r in retry_reasons
            )

            if needs_retry and (attempt < MAX_RETRIES or has_structural):
                # === 에러 분류 ===
                structural_errors = []  # binary 비교, 변수 없는 제약
                structural_error_names = set()
                non_structural_errors = []  # 미바인딩, set 매핑 등

                for r in retry_reasons:
                    is_structural = (
                        ("binary" in r.lower() or "이진" in r or "0/1" in r)
                        or ("변수가 없" in r or "양쪽 모두 변수" in r)
                    )
                    if is_structural:
                        structural_errors.append(r)
                        # 에러 메시지에서 제약 이름 추출 (Constraint 'xxx':)
                        if "Constraint '" in r:
                            cname = r.split("Constraint '")[1].split("'")[0]
                            structural_error_names.add(cname)
                    else:
                        non_structural_errors.append(r)

                # === 구조 에러가 있으면 repair_constraints 시도 ===
                if structural_error_names:
                    logger.info(
                        f"Structural errors in {len(structural_error_names)} constraints: "
                        f"{structural_error_names} — attempting repair"
                    )

                    # 에러 제약 정보 수집
                    error_constraint_list = []
                    valid_constraint_names = []
                    for c in model.get("constraints", []):
                        cname = c.get("name", "")
                        if cname in structural_error_names:
                            # 해당 에러 메시지 찾기
                            err_msg = next(
                                (r for r in structural_errors if cname in r),
                                "구조 에러"
                            )
                            error_constraint_list.append({
                                "name": cname,
                                "description": c.get("description", ""),
                                "expression": c.get("expression", ""),
                                "error_reason": err_msg,
                            })
                        else:
                            valid_constraint_names.append(cname)

                    # repair_constraints 호출
                    try:
                        repair_result = await repair_constraints(
                            model=model,
                            error_constraints=error_constraint_list,
                            valid_constraint_names=valid_constraint_names,
                        )

                        if repair_result.get("success"):
                            # === 모델에 수정 결과 병합 ===
                            # 1) 새 변수 추가
                            for new_var in repair_result.get("added_variables", []):
                                model["variables"].append(new_var)
                                logger.info(f"  Added variable: {new_var.get('id')}")

                            # 2) 제거 대상 제약 삭제
                            removed = set(repair_result.get("removed_constraints", []))
                            if removed:
                                model["constraints"] = [
                                    c for c in model["constraints"]
                                    if c.get("name") not in removed
                                ]
                                logger.info(f"  Removed constraints: {removed}")

                            # 3) 에러 제약 교체
                            replaced_map = {
                                rc.get("name"): rc
                                for rc in repair_result.get("replaced_constraints", [])
                            }
                            for i, c in enumerate(model["constraints"]):
                                cname = c.get("name", "")
                                if cname in replaced_map:
                                    model["constraints"][i] = replaced_map[cname]
                                    logger.info(f"  Replaced constraint: {cname}")

                            # 4) 새 제약 추가
                            for new_con in repair_result.get("added_constraints", []):
                                model["constraints"].append(new_con)
                                logger.info(f"  Added constraint: {new_con.get('name')}")

                            logger.info(
                                f"Repair complete: {len(replaced_map)} replaced, "
                                f"{len(repair_result.get('added_constraints', []))} added, "
                                f"{len(removed)} removed"
                            )

                            # ★ 후처리 1: repair가 추가한 제약에서 참조하는 미등록 변수 자동 등록
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
                            # 단, generate_math_model을 다시 호출하지 않도록 플래그 설정
                            retry_feedback = "__REPAIR_DONE__"
                            continue

                        else:
                            logger.warning(
                                f"Repair failed: {repair_result.get('error')} — "
                                f"falling back to full regeneration"
                            )
                    except Exception as e:
                        logger.warning(f"Repair exception: {e} — falling back to full regeneration")

                # === 구조 에러 repair 실패 또는 비구조 에러만 → 기존 전체 재생성 ===
                if attempt >= MAX_RETRIES:
                    # 마지막 attempt면 전체 재생성 불가 → 루프 탈출
                    logger.warning(f"Max retries reached ({attempt}) — proceeding with current model")
                    break

                fix_instructions = []

                has_foreach_error = any("for_each" in r and "동일 set" in r for r in retry_reasons)
                if has_foreach_error:
                    fix_instructions.append(
                        "★ 제약 구조 수정 필수: for_each='j in J'(승무원별 반복), "
                        "sum over='i in I'(운행별 합산)으로 분리. "
                        "예: for_each='j in J', lhs.sum.over='i in I', lhs.sum.index='[j,i]'"
                    )

                has_unnamed = any("Unnamed" in r for r in retry_reasons)
                if has_unnamed:
                    fix_instructions.append(
                        "★ 비정형 파일의 Unnamed 컬럼 대신 __summary 집계 테이블을 사용하세요"
                    )

                has_st = any("Set 'ST'" in r for r in retry_reasons)
                if has_st:
                    fix_instructions.append(
                        "★ 불필요한 Set을 제거하고 데이터 가이드의 권장 Set 매핑만 사용하세요"
                    )

                has_unbound = any("미바인딩 파라미터" in r for r in retry_reasons)
                if has_unbound:
                    fix_instructions.append(
                        "★ 미바인딩 파라미터는 데이터에서 source를 찾거나 적절한 default_value를 설정하세요"
                    )

                has_set_size = any("크기를 결정할 수 없음" in r for r in retry_reasons)
                if has_set_size:
                    fix_instructions.append(
                        "★ Set 정의 시 데이터 가이드의 권장 매핑을 따르세요"
                    )

                has_binary_error = any(
                    ("binary" in r.lower() or "이진" in r or "0/1" in r)
                    and ("비교" in r or "compare" in r.lower() or "직접" in r)
                    for r in retry_reasons
                )
                if has_binary_error:
                    fix_instructions.append(
                        "★ [CRITICAL] binary(0/1) 변수를 시간/횟수 상수와 직접 비교 금지. "
                        "시간 제약은 sum(duration[i]*x[j,i], over=I) <= max_hours 형태로 표현하거나, "
                        "별도 연속변수(work_hours[j])를 정의하여 사용할 것"
                    )

                retry_feedback = (
                    "이전 모델에 다음 문제가 있어 재생성합니다:\n"
                    + "\n".join(f"- {r}" for r in retry_reasons[:8])
                    + "\n\n수정 지시:\n"
                    + "\n".join(fix_instructions)
                )
                logger.warning(
                    f"Gate2 triggered retry (attempt {attempt}): {retry_reasons}"
                )
                continue


            # ★ user_input_required 파라미터 체크
            # source_file/source_column이 있는 파라미터는 데이터에서 바인딩 가능하므로 제외
            need_input_params = [
                p.get("id", p.get("name", ""))
                for p in model.get("parameters", [])
                if p.get("user_input_required")
                and not p.get("source_file")
                and not p.get("source_column")
            ]
            if need_input_params:
                logger.info(f"user_input_required 파라미터 {len(need_input_params)}개: {need_input_params}")
                state.math_model = model
                state.pending_param_inputs = need_input_params
                save_session_state(project_id, state)

                param_lines = []
                for pname in need_input_params:
                    # 파라미터 설명 추출
                    pdesc = ""
                    for p in model.get("parameters", []):
                        if p.get("id") == pname or p.get("name") == pname:
                            pdesc = p.get("description", "")
                            break
                    param_lines.append(f"  - **{pname}**: {pdesc}" if pdesc else f"  - **{pname}**")

                return {
                    "type": "param_input",
                    "text": (
                        f"📐 수학 모델이 생성되었으나, **{len(need_input_params)}개 파라미터**의 값을 "
                        f"데이터에서 자동으로 찾을 수 없습니다.\n\n"
                        f"아래 파라미터의 값을 입력해 주세요:\n"
                        + "\n".join(param_lines)
                        + "\n\n예시: `max_work_hours_per_trip=660, min_layover_time=30`"
                    ),
                    "data": {
                        "view_mode": "param_input",
                        "pending_params": need_input_params,
                        "math_model": model,
                    },
                    "options": [
                        {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    ],
                }

            # 검증 통과 또는 최대 재시도 도달
            final_model = model
            final_validation = validation
            final_gate2 = gate2_result
            break

        model = final_model
        validation = final_validation
        gate2_result = final_gate2

        # 교정된 변수 수를 validation에 추가
        if gate2_result.get("corrections"):
            if not validation.get("warnings"):
                validation["warnings"] = []
            for key, val in gate2_result["corrections"].items():
                if isinstance(val, dict) and 'old' in val and 'new' in val:
                    validation["warnings"].append(f"자동 교정: {key} {val['old']} → {val['new']}")

        # Gate 2 경고를 validation에 병합
        if gate2_result.get("warnings"):
            if not validation.get("warnings"):
                validation["warnings"] = []
            validation["warnings"].extend(gate2_result["warnings"])

        # Gate 2 오류가 있으면 사용자에게 알림
        if gate2_result.get("errors"):
            if not validation.get("errors"):
                validation["errors"] = []
            validation["errors"].extend(gate2_result["errors"])

        # 세션에 저장
        state.math_model = model
        state.math_model_confirmed = False
        state.last_executed_skill = "MathModelSkill"

        # 사용자에게 보여줄 요약
        summary = summarize_model(model)

        # 검증 경고 표시
        warning_lines = []
        if validation.get("warnings"):
            warning_lines.append("\n### ⚠️ 검증 경고")
            for w in validation["warnings"]:
                warning_lines.append(f"- {w}")

        meta = model.get("metadata", {})
        var_count = meta.get("estimated_variable_count", "?")
        con_count = meta.get("estimated_constraint_count", "?")

        return {
            "type": "analysis",
            "text": (
                f"📐 **수학 모델이 생성되었습니다.**\n\n"
                f"추정 변수 수: **{var_count}개**\n"
                f"추정 제약 수: **{con_count}개**\n\n"
                f"오른쪽 패널에서 상세 모델을 확인하고, 맞으면 '모델 확정'을 눌러주세요."
            ),
            "data": {
                "view_mode": "math_model",
                "math_model": model,
                "math_model_summary": summary + "\n".join(warning_lines)
            },
            "options": [
                {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                {"label": "✏️ 목적함수 변경", "action": "send", "message": "목적함수를 변경하고 싶어요"},
                {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
            ],
        }

    except Exception as e:
        logger.error(f"MathModelSkill failed: {e}", exc_info=True)
        return error_response("수학 모델 생성 중 오류가 발생했습니다.")


async def skill_show_math_model(session: CrewSession, project_id: str, message: str, params: Dict
) -> Dict:
    state = session.state
    if state.math_model:
        summary = summarize_model(state.math_model)
        if state.math_model_confirmed:
            return {
                "type": "analysis",
                "text": "📐 **확정된 수학 모델입니다.**",
                "data": {
                    "view_mode": "math_model",
                    "math_model": state.math_model,
                    "math_model_summary": summary,
                },
                "options": [
                    {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                ],
            }
        else:
            return {
                "type": "analysis",
                "text": "📐 **이전에 생성된 수학 모델입니다.**\n\n확인 후 다음 단계를 진행해 주세요.",
                "data": {
                    "view_mode": "math_model",
                    "math_model": state.math_model,
                    "math_model_summary": summary,
                },
                "options": [
                    {"label": "✅ 모델 확정", "action": "send", "message": "수학 모델 확정"},
                    {"label": "🔄 모델 재생성", "action": "send", "message": "수학 모델 다시 생성해줘"},
                    {"label": "📊 분석 결과", "action": "send", "message": "분석 결과 보여줘"},
                ],
            }
    return {
        "type": "warning",
        "text": "아직 생성된 수학 모델이 없습니다. 먼저 수학 모델을 생성해 주세요.",
        "data": None,
        "options": [
            {"label": "📐 수학 모델 생성", "action": "send", "message": "수학 모델 생성해줘"},
        ],
    }


async def handle_math_model_confirm(model, session: CrewSession, project_id: str, message: str, current_tab: Optional[str] = None
) -> Dict:
    state = session.state
    msg = message.lower()

    # IntentClassifier fast_path → 키워드 fallback
    from core.platform.intent_classifier import get_intent_classifier, log_intent
    _ic = get_intent_classifier()
    _mm_intent = _ic.fast_path("math_model", message)
    if _mm_intent:
        log_intent(project_id, message, _mm_intent, skill_name="math_model")
    _is_confirm = _mm_intent and _mm_intent.intent == "confirm"
    _is_regenerate = _mm_intent and _mm_intent.intent == "regenerate"

    if not _mm_intent:
        # fallback: 기존 키워드 매칭
        if "확정" in msg or "확인" in msg or "맞" in msg:
            _is_confirm = True
        elif "다시" in msg or "재생성" in msg:
            _is_regenerate = True

    # 모델 확정
    if _is_confirm:
        state.math_model_confirmed = True
        meta = state.math_model.get("metadata", {}) if state.math_model else {}

        # Save model version
        try:
            pid = int(project_id)
            meta = state.math_model.get("metadata", {}) if state.math_model else {}
            domain = state.domain_override or state.detected_domain
            obj_func = ""
            if state.math_model and "formulation" in state.math_model:
                obj_func = state.math_model["formulation"].get("objective", {}).get("description", "")
            mv = create_model_version(
                project_id=pid,
                dataset_version_id=getattr(state, "current_dataset_version_id", None),
                model_json=state.math_model,
                domain_type=domain,
                objective_type=meta.get("problem_type", "unknown"),
                objective_summary=obj_func[:200] if obj_func else None,
                variable_count=meta.get("estimated_variable_count"),
                constraint_count=meta.get("estimated_constraint_count"),
                description="모델 확정",
            )
            state.current_model_version_id = mv.id
        except Exception as ve:
            logger.warning(f"Failed to create model version: {ve}")
        var_count = meta.get("estimated_variable_count", "?")
        return {
            "type": "analysis",
            "text": (
                f"✅ **수학 모델이 확정되었습니다.**\n\n"
                f"변수 규모({var_count}개)를 기반으로 솔버를 추천합니다."
            ),
            "data": {
                "view_mode": "math_model",
                "math_model": state.math_model,
                "math_model_summary": summarize_model(state.math_model) if state.math_model else None,
            },
            "options": [
                {"label": "⚡ 솔버 추천", "action": "send", "message": "솔버 추천해줘"},
            ],
        }

    # 재생성 요청
    if _is_regenerate:
        state.reset_from_math_model()
        return await skill_math_model(model, session, project_id, message, {})

    # 목적함수/제약 변경 요청 → 문제 정의 단계로 돌아감
    # (목적함수 변경 시 제약조건·파라미터 재구성이 필요하므로)
    if "목적" in msg or ("변경" in msg and any(kw in msg for kw in ["목적", "제약", "constraint", "objective"])):
        state.reset_from_math_model()
        # problem_defined를 False로 되돌려야 _handle_user_response로 진입
        # (problem_definition_proposed는 유지 → 기존 제안 기반으로 변경 처리)
        state.problem_defined = False
        save_session_state(project_id, state)
        from domains.crew.skills.problem_definition import skill_problem_definition
        return await skill_problem_definition(model, session, project_id, message, {})

    # 기타 → 일반 처리
    return await skill_general(model, session, project_id, message, {})


