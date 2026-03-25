# ============================================================
# engine/pre_decision.py — v3.0
# ============================================================
# Pre-Decision Engine: 수학 모델 기반 솔버 추천 + 실행 전략 추천
# - Solver Registry에서 솔버 정보 로드
# - Problem Profile 기반 Scoring Engine으로 솔버 추천
# - 수학 모델 특성 분석 → 실행 전략(Execution Strategy) 생성
# ============================================================

import logging
from typing import Dict, Optional, List
from .solver_registry import (
    recommend_solvers,
    build_problem_profile,
    SolverRegistry,
    estimate_time,
    estimate_cost,
)

logger = logging.getLogger(__name__)


# ============================================================
# 메인 진입점
# ============================================================
async def run_pre_decision_analysis(
    math_model: Dict,
    priority: str = "auto",
    data_facts: Optional[Dict] = None,
    project_id: Optional[str] = None,
) -> Dict:
    """
    확정된 수학 모델을 기반으로 최적 솔버 + 실행 전략을 추천

    Returns:
        problem_profile, recommended_solvers, top_recommendation,
        execution_strategies, recommended_strategy
    """
    logger.info(f"Pre-Decision analysis started (priority={priority})")

    # 1. 솔버 레지스트리 로드
    solvers = SolverRegistry.get_all()
    logger.info(f"Loaded {len(solvers)} solvers from registry")

    # 2. DB에서 활성화된 솔버 조회
    enabled_ids = _get_enabled_solver_ids()
    if enabled_ids is not None:
        logger.info(f"Enabled solvers: {enabled_ids}")

    # 3. 솔버 추천 실행
    result = recommend_solvers(
        math_model=math_model,
        priority=priority,
        data_facts=data_facts,
        enabled_solver_ids=enabled_ids,
    )

    # 4. 로그
    profile = result.get("problem_profile", {})
    top = result.get("top_recommendation")
    logger.info(
        f"Problem profile: vars={profile.get('variable_count')}, "
        f"constraints={profile.get('constraint_count')}, "
        f"types={profile.get('variable_types')}"
    )
    if top:
        logger.info(
            f"Top recommendation: {top['provider']} {top['solver_name']} "
            f"(score={top['total_score']}, suitability={top['suitability']})"
        )

    # 5. 솔버 목록 포맷팅
    recommendations = result.get("recommendations", [])
    formatted_solvers = []
    for rec in recommendations:
        if rec["total_score"] < 20:
            continue
        formatted_solvers.append({
            "solver_id": rec["solver_id"],
            "provider": rec["provider"],
            "solver_name": rec["solver_name"],
            "solver_type": rec.get("model_type", ""),
            "category": rec["category"],
            "suitability": rec["suitability"],
            "total_score": rec["total_score"],
            "scores": rec["scores"],
            "reasons": rec["reasons"],
            "warnings": rec["warnings"],
            "description": rec["description"],
            "strengths": rec.get("strengths", []),
            "weaknesses": rec.get("weaknesses", []),
            "typical_time_seconds": rec.get("typical_time_seconds", []),
            "estimated_time": rec.get("estimated_time", []),
            "estimated_cost": rec.get("estimated_cost", []),
        })

    # 6. 실행 전략 생성
    model_analysis = _analyze_math_model(math_model)
    strategies = _generate_execution_strategies(
        model_analysis=model_analysis,
        profile=profile,
        solvers=formatted_solvers,
        priority=priority,
    )

    # 7. 최적 전략 선택
    recommended_strategy = strategies[0] if strategies else None

    logger.info(
        f"Generated {len(strategies)} execution strategies. "
        f"Recommended: {recommended_strategy['strategy_id'] if recommended_strategy else 'none'}"
    )

    return {
        "problem_profile": profile,
        "priority": priority,
        "recommended_solvers": formatted_solvers,
        "top_recommendation": formatted_solvers[0] if formatted_solvers else None,
        "execution_strategies": strategies,
        "recommended_strategy": recommended_strategy,
        "model_analysis": model_analysis,
        "summary": _build_summary(profile, formatted_solvers, recommended_strategy),
    }


# ============================================================
# 수학 모델 특성 분석
# ============================================================

def _get_set_size(set_def: Dict) -> int:
    """Set 정의에서 크기를 결정하는 헬퍼"""
    # 1. source_type: "range"
    if set_def.get("source_type") == "range":
        size = set_def.get("size", 0)
        if size > 0:
            return size
    # 2. elements
    elements = set_def.get("elements", [])
    if elements:
        return len(elements)
    # 3. explicit values
    values = set_def.get("values", [])
    if values:
        return len(values)
    # 4. default_size (YAML에서 range source의 기본 크기)
    default_size = set_def.get("default_size", 0)
    if default_size > 0:
        return int(default_size)
    # 5. 크기를 결정할 수 없음
    return 0


def _analyze_math_model(math_model: Dict) -> Dict:
    """수학 모델을 분석하여 전략 결정에 필요한 특성을 추출"""

    metadata = math_model.get("metadata", {})
    variables = math_model.get("variables", [])
    constraints = math_model.get("constraints", [])
    sets = math_model.get("sets", [])
    objective = math_model.get("objective", {})

    # 변수 타입 분석
    var_types = set()
    has_binary = False
    has_integer = False
    has_continuous = False
    for v in variables:
        vtype = v.get("type", "").lower()
        var_types.add(vtype)
        if vtype == "binary":
            has_binary = True
        elif vtype == "integer":
            has_integer = True
        elif vtype == "continuous":
            has_continuous = True

    # 제약조건 분석
    hard_constraints = [c for c in constraints if c.get("category", c.get("priority")) == "hard"]
    soft_constraints = [c for c in constraints if c.get("category", c.get("priority")) == "soft"]

    # 변수 규모 - 세트 크기에서 직접 계산 (LLM 추정치보다 정확)
    sets_map = {s.get("id"): s for s in sets}
    calculated_var_count = 0
    for v in variables:
        indices = v.get("indices", [])
        if not indices:
            calculated_var_count += 1
        else:
            product = 1
            for idx_id in indices:
                set_def = sets_map.get(idx_id, {})
                set_size = _get_set_size(set_def)
                if set_size > 0:
                    product *= set_size
                else:
                    product = 0
                    break
            if product > 0:
                calculated_var_count += product

    llm_estimate = metadata.get("estimated_variable_count", 0)
    var_count = calculated_var_count if calculated_var_count > 0 else llm_estimate
    if calculated_var_count > 0 and llm_estimate > 0 and abs(calculated_var_count - llm_estimate) > llm_estimate * 0.5:
        logger.warning(f"Variable count mismatch: calculated={calculated_var_count}, LLM_estimate={llm_estimate}. Using calculated.")
    # SP(Set Partitioning) 문제의 실제 변수 수는 컬럼 생성 후 결정됨
    # 여기서의 추정은 수학 모델 기반이며, SP 컴파일 후 실제 값과 다를 수 있음
    logger.info(f"Problem profile: vars={var_count} (pre-SP estimate, actual may differ after column generation)")

    constraint_count = metadata.get("estimated_constraint_count", len(constraints))

    # 문제 복잡도 판단
    is_pure_binary = has_binary and not has_integer and not has_continuous
    is_mixed = (has_binary or has_integer) and has_continuous
    is_large_scale = var_count > 10000
    is_very_large = var_count > 100000
    has_many_constraints = constraint_count > 50

    # QUBO 변환 가능성
    qubo_convertible = is_pure_binary and not has_many_constraints
    qubo_with_penalty = has_binary and len(hard_constraints) <= 20

    # 문제 분할 가능성 (인덱스 구조 분석)
    decomposable = False
    decomposition_hint = None
    for v in variables:
        indices = v.get("indices", [])
        if len(indices) >= 2:
            decomposable = True
            decomposition_hint = f"변수 '{v['id']}'의 인덱스 {indices}를 기준으로 서브문제 분할 가능"
            break

    return {
        "variable_types": list(var_types),
        "has_binary": has_binary,
        "has_integer": has_integer,
        "has_continuous": has_continuous,
        "is_pure_binary": is_pure_binary,
        "is_mixed": is_mixed,
        "variable_count": var_count,
        "constraint_count": constraint_count,
        "hard_constraint_count": len(hard_constraints),
        "soft_constraint_count": len(soft_constraints),
        "is_large_scale": is_large_scale,
        "is_very_large": is_very_large,
        "qubo_convertible": qubo_convertible,
        "qubo_with_penalty": qubo_with_penalty,
        "decomposable": decomposable,
        "decomposition_hint": decomposition_hint,
        "objective_type": objective.get("type", "minimize"),
        "has_alternatives": bool(objective.get("alternatives")),
    }


# ============================================================
# 실행 전략 생성
# ============================================================
def _generate_execution_strategies(
    model_analysis: Dict,
    profile: Dict,
    solvers: List[Dict],
    priority: str,
) -> List[Dict]:
    """수학 모델 분석 결과를 기반으로 실행 전략 후보를 동적 생성"""

    if not solvers:
        return []

    strategies = []

    #  솔버를 카테고리별로 분류 
    by_category: Dict[str, List[Dict]] = {}
    for s in solvers:
        cat = s.get("category", "unknown")
        by_category.setdefault(cat, []).append(s)

    classical_solvers = by_category.get("classical", [])
    gpu_solvers = by_category.get("classical_gpu", [])
    hybrid_solvers = by_category.get("quantum_hybrid", [])
    native_solvers = by_category.get("quantum_native", [])
    preprocessors = classical_solvers + gpu_solvers  # 전처리 가능한 솔버

    var_count = model_analysis.get("variable_count", 0)
    is_mixed = model_analysis.get("is_mixed", False)
    is_large_scale = model_analysis.get("is_large_scale", False)
    decomposable = model_analysis.get("decomposable", False)

    # 
    # Strategy A: 단일 솔버 (상위 N개)
    # 
    max_single = min(2, len(solvers))
    for i in range(max_single):
        top = solvers[i]
        label = "최고 점수 솔버" if i == 0 else f"{i+1}순위 솔버"
        strategies.append({
            "strategy_id": f"single_best_{i+1}",
            "strategy_type": "single",
            "name": f"단일 솔버: {top['solver_name']}",
            "description": f"{label} {top['solver_name']}으로 전체 문제를 직접 해결합니다.",
            "pros": ["구현 단순", "오버헤드 없음", f"적합도: {top.get('suitability', '-')}"],
            "cons": _get_single_cons(top, model_analysis),
            "estimated_time": top.get("estimated_time", []),
            "estimated_cost": top.get("estimated_cost", []),
            "confidence": top["total_score"],
            "steps": [
                {
                    "step_id": "step_1",
                    "step_order": 1,
                    "solver_id": top["solver_id"],
                    "solver_name": top["solver_name"],
                    "provider": top["provider"],
                    "role": "main_solver",
                    "input_type": "full_problem",
                    "description": f"{top['solver_name']}으로 전체 문제 해결",
                    "parallel_group": None,
                }
            ],
        })

    # 
    # Strategy B: 순차 하이브리드 (전처리 + 양자)
    #   - 조건: 대규모 문제 + 전처리 솔버 + 양자 솔버 존재
    #   - 모든 (preprocessor, quantum) 쌍 자동 생성
    # 
    if is_large_scale and preprocessors and (hybrid_solvers or native_solvers):
        quantum_pool = hybrid_solvers + native_solvers
        for pre in preprocessors:
            for qpu in quantum_pool:
                if pre["solver_id"] == qpu["solver_id"]:
                    continue
                pre_type = "GPU 병렬 처리" if "gpu" in pre.get("category", "") else "CPU 연산"
                strategies.append({
                    "strategy_id": f"seq_{pre['solver_id']}_{qpu['solver_id']}",
                    "strategy_type": "sequential_hybrid",
                    "name": f"순차 하이브리드: {pre['solver_name']} \u2192 {qpu['solver_name']}",
                    "description": (
                        f"1단계: {pre['solver_name']}의 {pre_type}로 초기해를 빠르게 구합니다. "
                        f"2단계: {qpu['solver_name']}으로 양자 최적화를 통해 해를 정제합니다."
                    ),
                    "pros": [
                        f"{pre_type}로 빠른 초기해 생성",
                        "양자 어닐링의 전역 최적화 능력 활용",
                        "대규모 문제에 효과적",
                    ],
                    "cons": [
                        "2단계 실행으로 총 시간 증가",
                        "단계 간 데이터 변환 필요",
                    ],
                    "estimated_time": _combine_times(pre, qpu, var_count),
                    "estimated_cost": _combine_costs(pre, qpu, var_count),
                    "confidence": round((pre["total_score"] + qpu["total_score"]) / 2 * 0.95, 1),
                    "steps": [
                        {
                            "step_id": "step_1",
                            "step_order": 1,
                            "solver_id": pre["solver_id"],
                            "solver_name": pre["solver_name"],
                            "provider": pre["provider"],
                            "role": "preprocessor",
                            "input_type": "full_problem",
                            "description": f"{pre['solver_name']}으로 초기해 생성",
                            "parallel_group": None,
                        },
                        {
                            "step_id": "step_2",
                            "step_order": 2,
                            "solver_id": qpu["solver_id"],
                            "solver_name": qpu["solver_name"],
                            "provider": qpu["provider"],
                            "role": "main_solver",
                            "input_type": "initial_solution",
                            "description": f"{qpu['solver_name']}으로 양자 최적화 정제",
                            "parallel_group": None,
                        },
                    ],
                })

    # 
    # Strategy C: 변수 분리 하이브리드 (혼합 변수 문제)
    #   - 조건: 혼합 변수 + classical + quantum 존재
    # 
    if is_mixed and preprocessors and (hybrid_solvers or native_solvers):
        cpu = preprocessors[0]  # 연속변수 처리에는 최고 점수 classical
        qpu = (hybrid_solvers + native_solvers)[0]  # 이진변수에는 최고 점수 quantum

        if cpu["solver_id"] != qpu["solver_id"]:
            strategies.append({
                "strategy_id": f"var_decomp_{cpu['solver_id']}_{qpu['solver_id']}",
                "strategy_type": "sequential_hybrid",
                "name": f"변수 분리: 연속변수({cpu['solver_name']}) + 이진변수({qpu['solver_name']})",
                "description": (
                    f"혼합 변수 문제를 분리합니다. "
                    f"연속변수는 {cpu['solver_name']} LP로, "
                    f"이진변수는 {qpu['solver_name']}으로 양자 최적화 후 결과를 통합합니다."
                ),
                "pros": [
                    "각 변수 타입에 최적화된 솔버 사용",
                    "연속변수 \u2192 LP (정확), 이진변수 \u2192 양자 (전역 탐색)",
                    "혼합 문제에 효과적",
                ],
                "cons": [
                    "변수 간 결합도가 높으면 분리 품질 저하",
                    "통합 단계에서 추가 최적화 필요",
                ],
                "estimated_time": _combine_times(cpu, qpu, var_count),
                "estimated_cost": _combine_costs(cpu, qpu, var_count),
                "confidence": round((cpu["total_score"] + qpu["total_score"]) / 2 * 0.90, 1),
                "steps": [
                    {
                        "step_id": "step_1",
                        "step_order": 1,
                        "solver_id": cpu["solver_id"],
                        "solver_name": cpu["solver_name"],
                        "provider": cpu["provider"],
                        "role": "sub_solver",
                        "input_type": "sub_problem_continuous",
                        "description": "연속변수 LP Relaxation 해결",
                        "parallel_group": "group_A",
                    },
                    {
                        "step_id": "step_2",
                        "step_order": 1,
                        "solver_id": qpu["solver_id"],
                        "solver_name": qpu["solver_name"],
                        "provider": qpu["provider"],
                        "role": "sub_solver",
                        "input_type": "sub_problem_binary",
                        "description": "이진변수 양자 최적화",
                        "parallel_group": "group_A",
                    },
                    {
                        "step_id": "step_3",
                        "step_order": 2,
                        "solver_id": cpu["solver_id"],
                        "solver_name": cpu["solver_name"],
                        "provider": cpu["provider"],
                        "role": "validator",
                        "input_type": "merged_solution",
                        "description": "서브 결과 통합 및 제약조건 검증",
                        "parallel_group": None,
                    },
                ],
            })

    # 
    # Strategy D: 문제 분할 (대규모 + 분할 가능)
    #   - 조건: decomposable + large_scale + quantum 존재
    # 
    if decomposable and is_large_scale and (hybrid_solvers or native_solvers):
        qpu = (hybrid_solvers + native_solvers)[0]
        cpu = preprocessors[0] if preprocessors else None
        hint = model_analysis.get("decomposition_hint", "")

        if cpu and cpu["solver_id"] != qpu["solver_id"]:
            strategies.append({
                "strategy_id": f"partition_{qpu['solver_id']}",
                "strategy_type": "sequential_hybrid",
                "name": f"문제 분할: 서브문제 분할 \u2192 {qpu['solver_name']} \u2192 병합",
                "description": (
                    f"대규모 문제를 서브문제로 분할하여 처리합니다. "
                    f"{hint + ' ' if hint else ''}"
                    f"각 서브문제를 {qpu['solver_name']}으로 해결한 후 "
                    f"{cpu['solver_name']}으로 결과를 병합\u00b7검증합니다."
                ),
                "pros": [
                    "대규모 문제를 QPU 처리 가능 규모로 축소",
                    "서브문제 간 병렬 처리 가능 (향후)",
                ] + ([f"분할 기준: {hint}"] if hint else []),
                "cons": [
                    "분할 경계에서 최적성 손실 가능",
                    "병합 단계에서 추가 최적화 필요",
                    "분할 알고리즘 품질에 의존",
                ],
                "estimated_time": _combine_times(cpu, qpu, var_count),
                "estimated_cost": _combine_costs(cpu, qpu, var_count),
                "confidence": round((cpu["total_score"] + qpu["total_score"]) / 2 * 0.85, 1),
                "steps": [
                    {
                        "step_id": "step_1",
                        "step_order": 1,
                        "solver_id": cpu["solver_id"],
                        "solver_name": cpu["solver_name"],
                        "provider": cpu["provider"],
                        "role": "preprocessor",
                        "input_type": "full_problem",
                        "description": "문제 분석 및 서브문제 분할",
                        "parallel_group": None,
                    },
                    {
                        "step_id": "step_2",
                        "step_order": 2,
                        "solver_id": qpu["solver_id"],
                        "solver_name": qpu["solver_name"],
                        "provider": qpu["provider"],
                        "role": "main_solver",
                        "input_type": "sub_problems",
                        "description": f"서브문제별 {qpu['solver_name']} 양자 최적화",
                        "parallel_group": "group_B",
                    },
                    {
                        "step_id": "step_3",
                        "step_order": 3,
                        "solver_id": cpu["solver_id"],
                        "solver_name": cpu["solver_name"],
                        "provider": cpu["provider"],
                        "role": "validator",
                        "input_type": "sub_solutions",
                        "description": "서브문제 결과 병합 및 전체 최적화 검증",
                        "parallel_group": None,
                    },
                ],
            })

    # ═══════════════════════════════════════════════════
    # Strategy: Objective-driven (v3.0 신규)
    # ═══════════════════════════════════════════════════
    objective_intent = profile.get("objective_intent", {})
    modeling_pattern = profile.get("modeling_pattern", "generic_mip")
    primary_goal = objective_intent.get("primary_goal", "minimize_count")

    if modeling_pattern == "set_partitioning" and classical_solvers:
        top_classical = classical_solvers[0]
        sp_strategy = {
            "strategy_id": "sp_column_generation",
            "strategy_type": "single",
            "name": "Column Generation + Set Partitioning",
            "description": (
                "Duty/Route Generator가 feasible column을 미리 생성하고, "
                "solver는 최적 조합만 선택합니다. "
                f"목적: {primary_goal}."
            ),
            "pros": ["시간 제약 사전 검증", "solver 부하 최소", "exact optimal"],
            "cons": ["Generator 품질에 의존"],
            "estimated_time": [3.0, 30.0],
            "estimated_cost": [0.0, 0.0],
            "confidence": top_classical["total_score"] + 5,  # SP 보너스
            "steps": [
                {
                    "step_id": "step_1",
                    "step_order": 1,
                    "solver_id": top_classical["solver_id"],
                    "solver_name": top_classical["solver_name"],
                    "provider": top_classical["provider"],
                    "role": "main_solver",
                    "input_type": "set_partitioning",
                    "description": "Column Generator → Set Partitioning → 최적 선택",
                    "parallel_group": None,
                }
            ],
        }
        strategies.insert(0, sp_strategy)

        # CQM hybrid가 있으면 비교 전략 추가
        if hybrid_solvers:
            top_hybrid = hybrid_solvers[0]
            strategies.append({
                "strategy_id": "sp_quantum_comparison",
                "strategy_type": "parallel_comparison",
                "name": f"Classical vs Quantum 비교 ({primary_goal})",
                "description": (
                    "동일한 Set Partitioning 문제를 CP-SAT과 D-Wave에서 동시 실행하여 비교합니다."
                ),
                "pros": ["양자 성능 벤치마크", "해 다양성 확보"],
                "cons": ["D-Wave API 비용 발생"],
                "estimated_time": [5.0, 60.0],
                "estimated_cost": [0.0, 0.05],
                "confidence": min(top_classical["total_score"], top_hybrid["total_score"]),
                "steps": [
                    {
                        "step_id": "classical",
                        "step_order": 1,
                        "solver_id": top_classical["solver_id"],
                        "solver_name": top_classical["solver_name"],
                        "provider": top_classical["provider"],
                        "role": "baseline",
                        "input_type": "set_partitioning",
                        "description": "CP-SAT exact solve (baseline)",
                        "parallel_group": "compare",
                    },
                    {
                        "step_id": "quantum",
                        "step_order": 1,
                        "solver_id": top_hybrid["solver_id"],
                        "solver_name": top_hybrid["solver_name"],
                        "provider": top_hybrid["provider"],
                        "role": "comparison",
                        "input_type": "set_partitioning",
                        "description": "D-Wave hybrid solve (비교)",
                        "parallel_group": "compare",
                    },
                ],
            })

    #
    # 중복 제거 + 정렬 + 상위 5개
    #
    seen_ids = set()
    unique = []
    for s in strategies:
        if s["strategy_id"] not in seen_ids:
            seen_ids.add(s["strategy_id"])
            unique.append(s)

    unique.sort(key=lambda s: s["confidence"], reverse=True)
    return unique[:5]



# ============================================================
# 헬퍼 함수
# ============================================================
def _find_solver(solvers: List[Dict], category_keyword: str) -> Optional[Dict]:
    """카테고리에 해당하는 최고 점수 솔버를 찾기 (유연한 매칭)"""
    for s in solvers:
        cat = s.get("category", "")
        if category_keyword in cat:
            return s
    return None


def _find_all_solvers(solvers: List[Dict], category_keyword: str) -> List[Dict]:
    """카테고리에 해당하는 모든 솔버를 점수순으로 반환"""
    return [s for s in solvers if category_keyword in s.get("category", "")]


def _get_single_cons(solver: Dict, analysis: Dict) -> List[str]:
    """단일 솔버 전략의 단점 생성"""
    cons = []
    if analysis["is_mixed"] and "quantum" in solver.get("category", ""):
        cons.append("혼합 변수(binary+continuous) 처리 시 변환 필요")
    if analysis["is_very_large"]:
        cons.append("대규모 문제로 처리 시간이 길어질 수 있음")
    if analysis["is_large_scale"] and "quantum_native" in solver.get("category", ""):
        cons.append("QPU 큐빗 수 제한으로 문제 임베딩 필요")
    if not cons:
        cons.append("특별한 단점 없음")
    return cons


def _combine_times(solver1: Dict, solver2: Dict, var_count: int) -> List[float]:
    """두 솔버의 예상 시간 합산"""
    t1 = solver1.get("estimated_time", [0, 0])
    t2 = solver2.get("estimated_time", [0, 0])
    if not t1 or len(t1) < 2:
        t1 = [0, 0]
    if not t2 or len(t2) < 2:
        t2 = [0, 0]
    return [round(t1[0] + t2[0], 1), round(t1[1] + t2[1], 1)]


def _combine_costs(solver1: Dict, solver2: Dict, var_count: int) -> List[float]:
    """두 솔버의 예상 비용 합산"""
    c1 = solver1.get("estimated_cost", [0, 0])
    c2 = solver2.get("estimated_cost", [0, 0])
    if not c1 or len(c1) < 2:
        c1 = [0, 0]
    if not c2 or len(c2) < 2:
        c2 = [0, 0]
    return [round(c1[0] + c2[0], 4), round(c1[1] + c2[1], 4)]


def _build_summary(profile: Dict, solvers: list, strategy: Optional[Dict] = None) -> str:
    """추천 결과 요약 텍스트 생성"""
    lines = []

    var_count = profile.get("variable_count", 0)
    constraint_count = profile.get("constraint_count", 0)
    var_types = profile.get("variable_types", [])
    problem_classes = profile.get("problem_classes", [])

    lines.append(f"변수 {var_count:,}개, 제약조건 {constraint_count}개")
    lines.append(f"변수 타입: {', '.join(var_types)}")
    lines.append(f"문제 유형: {', '.join(problem_classes)}")

    if solvers:
        top = solvers[0]
        lines.append("")
        lines.append(f"최적 솔버: {top['provider']} — {top['solver_name']}")
        lines.append(f"적합도: {top['suitability']} (점수: {top['total_score']})")

    if strategy:
        lines.append("")
        lines.append(f"추천 전략: {strategy['name']}")
        lines.append(f"전략 유형: {strategy['strategy_type']}")
        lines.append(f"실행 단계: {len(strategy['steps'])}단계")

    return "\n".join(lines)


# ============================================================
# DB 헬퍼
# ============================================================
def _get_enabled_solver_ids() -> Optional[List[str]]:
    """DB에서 활성화된 솔버 ID 목록 조회"""
    try:
        from core.database import SessionLocal
        from core import models

        db = SessionLocal()
        try:
            rows = db.query(models.SolverSettingDB).all()
            if not rows:
                return None
            enabled = [r.solver_id for r in rows if r.enabled]
            if not enabled:
                return None
            return enabled
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to load solver settings: {e}")
        return None