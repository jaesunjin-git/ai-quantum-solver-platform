import shutil, py_compile, json, os

# ═══════════════════════════════════════════════
# Part 1: prompts/math_model.yaml 재작성
# ═══════════════════════════════════════════════
YAML_PATH = 'prompts/math_model.yaml'
shutil.copy2(YAML_PATH, YAML_PATH + '.bak')

NEW_YAML = r'''version: '2.0'
description: 수학 모델 생성용 프롬프트 (YAML 기반 템플릿)

system: 당신은 최적화 문제의 수학적 모델링 전문가입니다.

rules:
- JSON만 출력하라. 마크다운 코드블록을 사용하지 마라.
- 아래 스키마를 정확히 따르라. 필드를 빠뜨리지 마라.
- variables는 솔버가 결정하는 의사결정 변수만 포함하라. 데이터 컬럼은 parameters에 넣어라.
- '의사결정 변수 예시: x[i,j]=승무원i를 운행j에 배정 여부, y[j]=승무원j 활성화 여부.'
- estimated_variable_count는 각 변수의 인덱스 집합 크기의 곱의 합으로 계산하라.
- sets의 elements는 빈 배열로 두고 source_file과 source_column만 명시하라.
- 제약조건은 반드시 모든 제약(하드+소프트)을 포함하라. 절대 생략하지 마라.
- 제약조건은 반드시 구조화된 lhs/operator/rhs 필드를 포함하라.
- expression도 Python식으로 반드시 작성하되, 컴파일러는 lhs/operator/rhs를 우선 사용한다.
- 'lhs와 rhs 노드 타입: value, var, param, sum, multiply, add, subtract.'
- 'sum 노드의 over는 "변수 in 집합" 형태 (예: "j in J").'
- coeff가 있는 sum은 coeff 필드에 param 노드를 넣어라.
- 'for_each는 제약 반복 인덱스 (예: "i in I" 또는 "i in I, j in J").'
- 데이터에 개별 항목 목록이 없고 총 개수만 있으면 source_type을 "range"로 설정하라.
- 'source_type 옵션: (1) 생략 또는 "column" = source_file+source_column에서 고유값 추출, (2) "range" = 1부터 size까지 정수 집합 자동 생성, (3) "explicit" = values 배열에 직접 나열.'
- operator는 반드시 비교연산자(==, <=, >=, <, >, !=)만 사용하라.
- '★ 단위 통일: 모든 시간 파라미터는 반드시 "분(minutes)" 단위. 예: 11시간=660, 5시간=300.'
- '★ value 필드: 데이터 파일에서 가져올 수 없는 상수만 넣어라.'
- '★ 금지 패턴: (1) binary 변수를 시간 상수와 직접 비교 금지. (2) soft 제약에 무의미한 식 금지. (3) 열차 단위(i)와 승무원 단위(j) 혼동 금지.'
- '★ 파라미터 id는 반드시 영문 snake_case (한국어 금지). name은 한국어 설명용.'
- '★ 1계층 파라미터는 시스템이 자동 주입. parameters 배열에 포함하지 마라. constraints/objective에서 id를 그대로 참조하라.'
- '★ 중복 파라미터 금지: 1계층 파라미터와 동일한 id를 다시 정의하지 마라.'

soft_constraint_rules:
- '소프트 제약은 priority="soft"로 설정하고, weight 필드에 가중치를 넣어라.'
- '소프트 제약은 slack 변수를 사용하여 위반을 허용한다. 컴파일러가 자동으로 slack을 생성한다.'
- '목적함수에 소프트 제약의 penalty를 명시적으로 포함하지 마라. 컴파일러가 자동 추가한다.'
- '소프트 제약의 lhs/operator/rhs는 하드 제약과 동일한 구조를 사용하라.'
- '소프트 제약이 "평균" 관련이면, sum()/count 형태로 표현하라.'

objective_rules:
- '목적함수는 반드시 confirmed_problem의 objective를 따르라.'
- '목적함수 expression에는 소프트 제약 penalty를 포함하지 마라 (컴파일러가 자동 추가).'
- 'min_duties: sum(y[j] for j in J)를 minimize.'
- 'min_total_cost: sum(cost[i,j]*x[i,j] for i,j)를 minimize.'

sections:
  confirmed_problem: |
    [확정된 문제 정의]
    문제 단계: {stage}
    세부 유형: {variant}

  objective: |
    [확정된 목적함수]
    {objective_text}

  hard_constraints: |
    [하드 제약조건 ({hard_count}개)]
    아래 제약은 반드시 모델에 포함하세요. priority="hard", weight=0으로 설정하세요.
    {hard_constraint_list}

  soft_constraints: |
    [소프트 제약조건 ({soft_count}개)]
    아래 제약은 priority="soft"로 설정하고, 각 weight 값을 사용하세요.
    소프트 제약도 lhs/operator/rhs 구조를 동일하게 사용합니다.
    {soft_constraint_list}

  constraint_templates: |
    [제약조건 JSON 템플릿 — 반드시 이 구조를 사용]
    {templates_text}

  required_variables: |
    [필수 추가 변수]
    아래 변수를 variables 배열에 반드시 포함하세요:
    {required_vars_text}

  data_columns: |
    [데이터 컬럼 (파라미터 아님)]
    아래는 파라미터가 아닌 데이터 컬럼입니다. parameters 배열에 넣지 마세요.
    {data_columns_text}

  parameters: |
    [1계층 파라미터 (시스템 자동 주입)]
    아래 {param_count}개 파라미터는 시스템이 자동 주입합니다:
    {param_list_text}
    (id 목록: {param_ids})

template: |
  {system}

  중요 규칙:
  {rules_text}

  소프트 제약 규칙:
  {soft_rules_text}

  목적함수 규칙:
  {objective_rules_text}

  출력 JSON 스키마:
  {model_schema}

  제약조건 작성 형식:
  {constraint_schema}

  {objective_instruction}

  [검증된 데이터 팩트]
  {data_facts}

  [사용 가능한 데이터 소스]
  {confirmed_section}

  {data_guide}

  [데이터 요약]
  {csv_summary}

  [분석 리포트]
  {analysis_report}

  [도메인]
  {domain}

  위 스키마의 모든 필드를 채워서 순수 JSON으로 반환하세요.

checklist: |
  출력 전 체크리스트:
  (1) parameters에 1계층 id가 있는가? 있으면 제거.
  (2) 모든 제약의 lhs/rhs에 var 노드가 있는가?
  (3) 모든 operator가 비교연산자인가?
  (4) 하드 제약 {hard_count}개 + 소프트 제약 {soft_count}개 = 총 {total_count}개 제약이 있는가?
  (5) 소프트 제약에 priority="soft"와 weight가 설정되었는가?
'''

with open(YAML_PATH, 'w', encoding='utf-8') as f:
    f.write(NEW_YAML)
print(f'[1] {YAML_PATH} rewritten ({len(NEW_YAML.splitlines())} lines)')


# ═══════════════════════════════════════════════
# Part 2: _build_modeling_prompt 교체
# ═══════════════════════════════════════════════
TARGET = 'engine/math_model_generator.py'
shutil.copy2(TARGET, TARGET + '.bak_prompt')

with open(TARGET, encoding='utf-8') as f:
    src = f.read()

# 기존 함수 찾기
import re
pattern = r'(def _build_modeling_prompt\(.*?\n)(.*?)(\n# ={10,}.*?JSON 파싱)'
match = re.search(pattern, src, re.DOTALL)
if not match:
    print('[FAIL] _build_modeling_prompt function not found')
    exit(1)

func_start = match.start()
func_end = match.end()
after_func = match.group(3)

NEW_FUNC = '''def _build_modeling_prompt(
    csv_summary: str,
    analysis_report: str,
    domain: str,
    user_objective: Optional[str] = None,
    data_facts: Optional[dict] = None,
    data_guide: str = "",
    confirmed_problem=None,
) -> str:
    """LLM에게 수학 모델 JSON 생성을 요청하는 프롬프트를 조립 (YAML 기반 v2)"""
    import json as _json

    # YAML 프롬프트 설정 로드
    config = load_yaml_prompt("crew", "math_model")
    system = config.get("system", "")
    rules = config.get("rules", [])
    rules_text = "\\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

    soft_rules = config.get("soft_constraint_rules", [])
    soft_rules_text = "\\n".join(f"  - {r}" for r in soft_rules)

    obj_rules = config.get("objective_rules", [])
    obj_rules_text = "\\n".join(f"  - {r}" for r in obj_rules)

    constraint_schema_text = get_constraint_schema_text()
    model_schema = _get_model_schema()
    sections = config.get("sections", {})

    # 목적함수 지시
    if user_objective:
        objective_instruction = (
            "[사용자 지정 목적함수]\\n"
            f"사용자가 다음 최적화 목표를 요청했습니다: \\"{user_objective}\\"\\n"
            "이 목표를 objective 기본값으로 설정하고, 다른 목표는 alternatives에 넣으세요."
        )
    else:
        objective_instruction = (
            "[목적함수 추론]\\n"
            "사용자가 명시적으로 목적함수를 지정하지 않았습니다.\\n"
            "데이터와 도메인 특성을 분석하여 가장 적절한 목적함수를 추론하세요."
        )

    # ── confirmed_problem 섹션 조립 ──
    confirmed_section = ""
    if confirmed_problem:
        # 파라미터 목록
        _cp_params = confirmed_problem.get("parameters", {})
        _param_lines = []
        for _pid, _pval in _cp_params.items():
            _v = _pval.get("default", _pval.get("value", "")) if isinstance(_pval, dict) else _pval
            _param_lines.append(f"  - {_pid} (value: {_v})")
        _param_list_text = "\\n".join(_param_lines) if _param_lines else "  (none)"
        _param_ids = ", ".join(_cp_params.keys())

        # 도메인 YAML에서 constraint_templates 로드
        _domain_yaml = _load_domain_yaml(domain)
        _ct = _domain_yaml.get("constraint_templates", {})

        _template_json_lines = []
        _required_vars = []
        _obj_template = None

        for _tid, _tdata in _ct.items():
            if _tid == "_note" or not isinstance(_tdata, dict):
                continue
            if _tdata.get("_type") == "objective_template":
                _obj_template = _tdata
                continue
            _clean = {k: v for k, v in _tdata.items() if k not in ("_note", "requires_variables")}
            _template_json_lines.append(
                f"  // {_tdata.get('description', _tid)}\\n"
                f"  {_json.dumps(_clean, ensure_ascii=False)}"
            )
            for _rv in _tdata.get("requires_variables", []):
                if _rv not in _required_vars:
                    _required_vars.append(_rv)

        _templates_text = (
            "아래 JSON 제약 구조를 constraints 배열에 그대로 넣으세요.\\n"
            "이름, 변수명, 파라미터명을 임의로 바꾸지 마세요.\\n\\n"
            + "\\n\\n".join(_template_json_lines)
        ) if _template_json_lines else "(없음)"

        _req_vars_text = "\\n".join(f"  - {rv}" for rv in _required_vars) if _required_vars else ""

        # 목적함수 텍스트
        _obj_text = "  (추론 필요)"
        if _obj_template:
            _obj_text = (
                f"  방향: {_obj_template.get('type', 'minimize')}\\n"
                f"  대상: {_obj_template.get('description', '')}\\n"
                f"  expression: {_json.dumps(_obj_template.get('expression', {}), ensure_ascii=False)}"
            )
            _alts = _obj_template.get("alternatives", [])
            if _alts:
                _alt_texts = [a.get("description", "") for a in _alts if isinstance(a, dict)]
                if _alt_texts:
                    _obj_text += f"\\n  대안: {', '.join(_alt_texts)}"
        elif confirmed_problem.get("objective"):
            _obj_info = confirmed_problem["objective"]
            _obj_text = f"  방향: {_obj_info.get('type', 'minimize')}\\n  대상: {_obj_info.get('description', _obj_info.get('target', ''))}"

        # 데이터 컬럼
        _data_cols = _domain_yaml.get("data_columns", {})
        _dc_lines = []
        if _data_cols:
            for _src_key, _src_info in _data_cols.items():
                if not isinstance(_src_info, dict):
                    continue
                _sf = _src_info.get("source_file", "")
                _cols = _src_info.get("columns", {})
                for _cn, _cd in _cols.items():
                    _dc_lines.append(f"  - {_cn}: {_cd} (source_file: {_sf})")

        # ★ 하드 제약 목록
        _hard_constraints = confirmed_problem.get("hard_constraints", {})
        _hard_lines = []
        for _hid, _hdata in _hard_constraints.items():
            if isinstance(_hdata, dict):
                _desc = _hdata.get("name_ko", _hdata.get("description", _hid))
                _type = _hdata.get("type", "")
                _hard_lines.append(f"  - {_hid}: {_desc} [type={_type}]")
            else:
                _hard_lines.append(f"  - {_hid}")
        _hard_list_text = "\\n".join(_hard_lines) if _hard_lines else "  (없음)"

        # ★ 소프트 제약 목록
        _soft_constraints = confirmed_problem.get("soft_constraints", {})
        _soft_lines = []
        for _sid, _sdata in _soft_constraints.items():
            if isinstance(_sdata, dict):
                _desc = _sdata.get("name_ko", _sdata.get("description", _sid))
                _weight = _sdata.get("weight", 1.0)
                _type = _sdata.get("type", "")
                _soft_lines.append(f"  - {_sid}: {_desc} [weight={_weight}, type={_type}]")
            else:
                _soft_lines.append(f"  - {_sid}")
        _soft_list_text = "\\n".join(_soft_lines) if _soft_lines else "  (없음)"

        _hard_count = len(_hard_constraints)
        _soft_count = len(_soft_constraints)
        _total_count = _hard_count + _soft_count

        # 섹션별 텍스트 조립
        _parts = []
        _parts.append(sections.get("confirmed_problem", "").format(
            stage=confirmed_problem.get("stage", ""),
            variant=confirmed_problem.get("variant", ""),
        ))
        _parts.append(sections.get("objective", "").format(objective_text=_obj_text))
        _parts.append(sections.get("hard_constraints", "").format(
            hard_count=_hard_count, hard_constraint_list=_hard_list_text
        ))
        _parts.append(sections.get("soft_constraints", "").format(
            soft_count=_soft_count, soft_constraint_list=_soft_list_text
        ))
        if _templates_text:
            _parts.append(sections.get("constraint_templates", "").format(templates_text=_templates_text))
        if _req_vars_text:
            _parts.append(sections.get("required_variables", "").format(required_vars_text=_req_vars_text))
        if _dc_lines:
            _parts.append(sections.get("data_columns", "").format(
                data_columns_text="\\n".join(_dc_lines)
            ))
        _parts.append(sections.get("parameters", "").format(
            param_count=len(_cp_params),
            param_list_text=_param_list_text,
            param_ids=_param_ids,
        ))

        confirmed_section = "\\n".join(_parts)

        # 목적함수 지시 업데이트
        if _obj_template or confirmed_problem.get("objective"):
            objective_instruction = f"[확정된 목적함수 — 아래를 따르세요]\\n{_obj_text}"

    # ── 최종 프롬프트 조립 (YAML template 사용) ──
    template = config.get("template", "")
    checklist = config.get("checklist", "")
    if confirmed_problem:
        _hard_count = len(confirmed_problem.get("hard_constraints", {}))
        _soft_count = len(confirmed_problem.get("soft_constraints", {}))
        _total_count = _hard_count + _soft_count
        checklist = checklist.format(
            hard_count=_hard_count, soft_count=_soft_count, total_count=_total_count
        )

    prompt = template.format(
        system=system,
        rules_text=rules_text,
        soft_rules_text=soft_rules_text,
        objective_rules_text=obj_rules_text,
        model_schema=model_schema,
        constraint_schema=constraint_schema_text,
        objective_instruction=objective_instruction,
        data_facts=_format_facts_for_model(data_facts),
        confirmed_section=confirmed_section,
        data_guide=data_guide,
        csv_summary=csv_summary[:4000],
        analysis_report=analysis_report[:3000],
        domain=domain,
    )

    if checklist:
        prompt += "\\n\\n" + checklist

    return prompt

'''

# 교체
new_src = src[:func_start] + NEW_FUNC + "\n" + after_func + src[func_end:]
# after_func가 이미 포함되어 있으므로 중복 제거
new_src = src[:func_start] + NEW_FUNC + src[func_end:]

with open(TARGET, 'w', encoding='utf-8') as f:
    f.write(new_src)

py_compile.compile(TARGET, doraise=True)
print(f'[2] {TARGET} _build_modeling_prompt replaced, syntax OK')

# 함수 줄 수 확인
new_lines = NEW_FUNC.strip().splitlines()
print(f'    New function: {len(new_lines)} lines (was 190 lines)')

print('\nDone!')
