# ============================================================
# engine/solver_registry.py — v2.0
# ============================================================
# Solver Registry: YAML 파일에서 솔버 정보를 로드하고
# Problem Profile 기반으로 적합한 솔버를 추천
#
# v1.0 → v2.0 변경 이력:
#   - build_problem_profile: 문제 클래스 확장 (permutation, subset, TSP 등)
#   - build_problem_profile: 제약조건 구조 분석 (nonlinear, all_different 등)
#   - build_problem_profile: data_facts 기반 변수 수 실측 보정
#   - score_solver: model_type 매칭 점수 추가 (NL 네이티브 보너스)
#   - score_solver: 구조 점수 재설계 (40+25+20+15)
#   - score_solver: Scale 점수 가우시안 커브
#   - score_solver: 제약조건 복잡도 보너스/페널티
#   - score_solver: exact vs approximate 대규모 문제 재조정
#   - recommend_solvers: data_facts 기반 변수 수 실측 보정
# ============================================================

import os
import yaml
import logging
from typing import List, Dict, Optional, Any
from pathlib import Path
import math

logger = logging.getLogger(__name__)

SOLVERS_DIR = Path(__file__).parent.parent / "configs" / "solvers"

# 점수 가중치 기본값
DEFAULT_WEIGHTS = {
    "auto": {"structure": 0.35, "scale": 0.30, "cost": 0.15, "speed": 0.20},
    "accuracy": {"structure": 0.50, "scale": 0.25, "cost": 0.05, "speed": 0.20},
    "speed": {"structure": 0.20, "scale": 0.20, "cost": 0.10, "speed": 0.50},
    "cost": {"structure": 0.20, "scale": 0.20, "cost": 0.50, "speed": 0.10},
}


class SolverRegistry:
    """솔버 YAML 파일을 로드하고 관리"""

    _solvers: List[Dict] = []
    _loaded: bool = False

    @classmethod
    def load(cls) -> List[Dict]:
        """configs/solvers/*.yaml에서 모든 솔버 로드"""
        if cls._loaded:
            return cls._solvers

        cls._solvers = []

        if not SOLVERS_DIR.exists():
            logger.warning(f"Solvers directory not found: {SOLVERS_DIR}")
            return cls._solvers

        for yaml_file in sorted(SOLVERS_DIR.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                provider = data.get("provider", "Unknown")
                api_config = data.get("api_config", {})

                for solver in data.get("solvers", []):
                    solver["provider"] = provider
                    solver["api_config"] = api_config
                    solver["_source_file"] = yaml_file.name
                    cls._solvers.append(solver)

            except Exception as e:
                logger.error(f"Failed to load solver config {yaml_file}: {e}")

        cls._loaded = True
        logger.info(f"Loaded {len(cls._solvers)} solvers from {SOLVERS_DIR}")
        return cls._solvers

    @classmethod
    def reload(cls):
        """캐시 초기화 후 다시 로드"""
        cls._loaded = False
        cls._solvers = []
        return cls.load()

    @classmethod
    def get_solver(cls, solver_id: str) -> Optional[Dict]:
        """ID로 솔버 조회"""
        cls.load()
        for s in cls._solvers:
            if s.get("id") == solver_id:
                return s
        return None

    @classmethod
    def get_all(cls) -> List[Dict]:
        """전체 솔버 목록"""
        return cls.load()


# ============================================================
# Problem Profile 생성 (v2.0 — 구조 분석 강화)
# ============================================================

# 문제 클래스 키워드 매핑 — YAML 외부 설정에서 로드
_PROBLEM_CLASS_KEYWORDS_PATH = Path(__file__).parent.parent / "configs" / "problem_class_keywords.yaml"

def _load_problem_class_keywords() -> Dict:
    if _PROBLEM_CLASS_KEYWORDS_PATH.exists():
        try:
            with open(_PROBLEM_CLASS_KEYWORDS_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("problem_classes", {})
        except Exception as e:
            logger.warning(f"Failed to load problem_class_keywords.yaml: {e}")
    # fallback: minimal built-in defaults
    return {
        "scheduling": ["schedule", "scheduling", "shift", "roster"],
        "routing": ["routing", "route", "TSP", "VRP"],
        "assignment": ["assign", "assignment", "matching"],
    }

def _load_constraint_structure_keywords() -> Dict:
    if _PROBLEM_CLASS_KEYWORDS_PATH.exists():
        try:
            with open(_PROBLEM_CLASS_KEYWORDS_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("constraint_structure", {})
        except Exception as e:
            logger.warning(f"Failed to load constraint_structure from YAML: {e}")
    return {
        "permutation": ["all_different", "allDiff", "permutation"],
        "nonlinear": ["quadratic", "nonlinear", "product"],
        "conditional": ["if ", "indicator", "implies"],
    }

_PROBLEM_CLASS_KEYWORDS = _load_problem_class_keywords()
_CONSTRAINT_STRUCTURE_KEYWORDS = _load_constraint_structure_keywords()


def build_problem_profile(math_model: Dict, data_facts: Optional[Dict] = None) -> Dict:
    """
    수학 모델에서 Problem Profile 추출 (v2.0)

    v2.0 변경:
    - 문제 클래스 키워드 확장 (10종)
    - 제약조건/변수 구조 기반 분류 추가
    - data_facts 기반 변수 수 실측 보정
    """

    # ── 변수 정보 ──
    variables = math_model.get("variables", math_model.get("decision_variables", []))
    sets = math_model.get("sets", [])
    sets_map = {s.get("id"): s for s in sets}

    var_types = set()
    has_multi_index_binary = False

    for v in variables:
        vtype = v.get("type", "binary").lower()
        var_types.add(vtype)
        indices = v.get("indices", [])
        if len(indices) >= 2 and vtype == "binary":
            has_multi_index_binary = True

    # ── 변수 수 계산 (3단계 우선순위) ──
    # 1. data_facts 기반 실측 (가장 정확)
    # 2. sets 정의 기반 계산
    # 3. LLM metadata 추정 (fallback)
    calculated_var_count = _calculate_variable_count(variables, sets_map, data_facts)

    metadata = math_model.get("metadata", math_model.get("estimation", {}))
    llm_estimated_vars = _parse_int(metadata.get("estimated_variable_count", 0))
    llm_estimated_constraints = _parse_int(metadata.get("estimated_constraint_count", 0))

    if calculated_var_count > 0:
        var_count = calculated_var_count
        var_count_source = "calculated"
        if llm_estimated_vars > 0 and abs(calculated_var_count - llm_estimated_vars) > llm_estimated_vars * 0.5:
            logger.info(
                f"Variable count: calculated={calculated_var_count:,} vs LLM={llm_estimated_vars:,} "
                f"(using calculated)"
            )
    else:
        var_count = llm_estimated_vars if llm_estimated_vars > 0 else len(variables)
        var_count_source = "llm_estimate" if llm_estimated_vars > 0 else "variable_definitions"

    # ── 제약조건 분석 ──
    constraints = math_model.get("constraints", [])
    hard_constraints = []
    soft_constraints = []
    for c in constraints:
        cat = str(c.get("category", c.get("priority", ""))).lower()
        if cat in ("soft", "선호"):
            soft_constraints.append(c)
        else:
            hard_constraints.append(c)

    has_constraints = len(constraints) > 0
    constraint_count = llm_estimated_constraints if llm_estimated_constraints > 0 else len(constraints)

    # ── 제약조건 구조 분석 (v2.0 신규) ──
    constraint_features = _analyze_constraint_structure(constraints)

    # ── 문제 도메인 & 클래스 ──
    domain = math_model.get("domain", "general")
    problem_name = math_model.get("problem_name", "")
    objective = math_model.get("objective", {})

    # 문제 클래스 추정 (v2.0: 확장된 키워드 + 구조 기반)
    problem_classes = _classify_problem(
        problem_name=problem_name,
        variables=variables,
        constraints=constraints,
        constraint_features=constraint_features,
        has_multi_index_binary=has_multi_index_binary,
        objective=objective,
    )

    # ── 목적함수 분석 (v2.0 신규) ──
    obj_expression = str(objective.get("expression", ""))
    is_nonlinear_obj = any(kw in obj_expression.lower() for kw in ["*", "quadratic", "min(", "max(", "abs("])

    # ── 목적함수 의도 분류 (v3.0 신규) ──
    objective_intent = _classify_objective_intent(objective)

    # ── 모델링 패턴 감지 (v3.0 신규) ──
    modeling_pattern = _detect_modeling_pattern(
        problem_classes, has_multi_index_binary, constraint_features, var_count
    )

    return {
        "variable_count": var_count,
        "variable_count_source": var_count_source,
        "constraint_count": constraint_count,
        "variable_types": list(var_types) if var_types else ["binary"],
        "has_constraints": has_constraints,
        "hard_constraint_count": len(hard_constraints),
        "soft_constraint_count": len(soft_constraints),
        "domain": domain,
        "problem_name": problem_name,
        "problem_classes": problem_classes,
        "variable_types_used": metadata.get("variable_types_used", list(var_types)),
        # v2.0 신규 필드
        "constraint_features": constraint_features,
        "is_nonlinear_objective": is_nonlinear_obj,
        "has_multi_index_binary": has_multi_index_binary,
        "data_facts_available": data_facts is not None and bool(data_facts.get("unique_counts")),
        # v3.0 신규 필드
        "objective_intent": objective_intent,
        "modeling_pattern": modeling_pattern,
    }


def _calculate_variable_count(
    variables: List[Dict], sets_map: Dict, data_facts: Optional[Dict] = None
) -> int:
    """
    변수 수 계산 (v2.0)

    우선순위:
    1. data_facts.unique_counts로 실제 데이터 크기 반영
    2. sets 정의의 elements/size/values
    3. 0 반환 (계산 불가)
    """
    total = 0

    for v in variables:
        indices = v.get("indices", [])
        if not indices:
            total += 1
            continue

        product = 1
        all_resolved = True
        for idx_id in indices:
            set_def = sets_map.get(idx_id, {})
            set_size = _resolve_set_size(set_def, data_facts)
            if set_size > 0:
                product *= set_size
            else:
                all_resolved = False
                break

        if all_resolved and product > 0:
            total += product

    return total


def _resolve_set_size(set_def: Dict, data_facts: Optional[Dict] = None) -> int:
    """
    Set 크기 결정 (v2.0 — data_facts 연동)

    우선순위:
    1. data_facts.unique_counts에서 source_column 기반 실측
    2. set_def.elements / values / size
    """
    # 1. data_facts에서 실측값 조회
    if data_facts:
        unique_counts = data_facts.get("unique_counts", {})
        source_file = set_def.get("source_file", "")
        source_col = set_def.get("source_column", "")

        if source_file and source_col and unique_counts:
            # "normalized/trips.csv.trip_id" or "trips.csv.trip_id" 형태 매칭
            for key, count in unique_counts.items():
                # key 예: "trips.csv.trip_id" or "normalized/trips.csv.trip_id"
                file_part = key.rsplit(".", 1)[0] if "." in key else ""
                col_part = key.rsplit(".", 1)[1] if "." in key else ""
                if col_part == source_col and (
                    source_file.endswith(file_part)
                    or file_part.endswith(source_file.replace("normalized/", ""))
                    or source_file.replace("normalized/", "") in file_part
                ):
                    logger.debug(f"Set '{set_def.get('id')}': data_facts match {key}={count}")
                    return int(count)

    # 2. set 정의에서 직접 크기 결정
    if set_def.get("source_type") == "range":
        size = set_def.get("size", 0)
        if size > 0:
            return size

    elements = set_def.get("elements", [])
    if elements:
        return len(elements)

    values = set_def.get("values", [])
    if values:
        return len(values)

    return 0


def _analyze_constraint_structure(constraints: List[Dict]) -> Dict:
    """제약조건에서 구조적 특성 추출 (v2.0 신규)"""
    features = {
        "has_permutation": False,
        "has_nonlinear": False,
        "has_conditional": False,
        "total_count": len(constraints),
    }

    for c in constraints:
        text = " ".join([
            str(c.get("expression", "")),
            str(c.get("description", "")),
            str(c.get("name", "")),
        ]).lower()

        for feature_key, keywords in _CONSTRAINT_STRUCTURE_KEYWORDS.items():
            if any(kw.lower() in text for kw in keywords):
                features[f"has_{feature_key}"] = True

    return features


# ── 목적함수 의도 분류 (v3.0) ──────────────────────────────

def _classify_objective_intent(objective: Dict) -> Dict:
    """
    목적함수에서 최적화 의도를 추출.

    primary_goal: 주 목표 (minimize_count / balance / efficiency / cost)
    secondary_goals: 부 목표 (multi-objective 대응)
    structure: 수식 구조 (linear / quadratic / nonlinear)
    """
    desc = str(objective.get("description", objective.get("description_ko", ""))).lower()
    expr = str(objective.get("expression", "")).lower()
    obj_id = str(objective.get("id", objective.get("name", ""))).lower()

    intent = {
        "primary_goal": "minimize_count",
        "secondary_goals": [],
        "structure": "linear",
        "is_multi_objective": False,
    }

    # ── primary_goal 분류 ──
    # ID 기반 (가장 정확)
    if "balance" in obj_id or "workload" in obj_id:
        intent["primary_goal"] = "balance"
    elif "efficiency" in obj_id:
        intent["primary_goal"] = "efficiency"
    elif "cost" in obj_id:
        intent["primary_goal"] = "cost"
    elif "minimize" in obj_id and ("dut" in obj_id or "crew" in obj_id or "count" in obj_id):
        intent["primary_goal"] = "minimize_count"
    # description 기반 fallback
    elif any(k in desc for k in ["균형", "공평", "균등", "balance", "fair"]):
        intent["primary_goal"] = "balance"
    elif any(k in desc for k in ["효율", "efficiency", "idle", "유휴"]):
        intent["primary_goal"] = "efficiency"
    elif any(k in desc for k in ["비용", "cost", "원가"]):
        intent["primary_goal"] = "cost"
    elif any(k in desc for k in ["최소", "minimize", "줄이", "인원", "수 최소"]):
        intent["primary_goal"] = "minimize_count"

    # ── secondary_goals 추출 ──
    secondary_keywords = {
        "efficiency": ["효율", "efficiency", "idle", "유휴"],
        "balance": ["균형", "균등", "balance", "공평"],
        "cost": ["비용", "cost", "수당"],
        "minimize_count": ["최소", "인원", "duty 수"],
    }
    for goal, keywords in secondary_keywords.items():
        if goal == intent["primary_goal"]:
            continue
        if any(k in desc for k in keywords):
            intent["secondary_goals"].append(goal)

    # ── 수식 구조 분석 ──
    if any(k in expr for k in ["**2", "variance", "std", "abs("]):
        intent["structure"] = "quadratic"
    elif any(k in expr for k in ["min(", "max(", "if "]):
        intent["structure"] = "nonlinear"

    # ── multi-objective 판정 ──
    if intent["secondary_goals"] or objective.get("alternatives"):
        intent["is_multi_objective"] = True

    return intent


# ── 모델링 패턴 감지 (v3.0) ────────────────────────────────

def _detect_modeling_pattern(
    problem_classes: set,
    has_multi_index_binary: bool,
    constraint_features: Dict,
    var_count: int,
) -> str:
    """
    문제 구조에서 최적 모델링 패턴을 감지.

    반환값:
      "set_partitioning"  — column generation / duty selection
      "assignment"        — I×J binary assignment
      "network_flow"      — routing / path cover / flow
      "generic_mip"       — 일반 MIP
    """
    # Set Partitioning 패턴: scheduling + 대규모 + binary assignment
    is_scheduling = "scheduling" in problem_classes
    is_assignment = "assignment" in problem_classes
    has_coverage = constraint_features.get("has_equality", False)

    # SP 패턴 조건: 스케줄링/배정 + 대규모 변수 + coverage 제약
    if is_scheduling and has_multi_index_binary and var_count > 1000 and has_coverage:
        return "set_partitioning"
    # coverage 없어도 대규모 scheduling이면 SP 후보
    if is_scheduling and has_multi_index_binary and var_count > 5000:
        return "set_partitioning"

    # Network flow 패턴: routing / TSP / path
    if any(c in problem_classes for c in ["routing", "TSP", "flow", "path_cover"]):
        return "network_flow"

    # Assignment 패턴: 2-index binary + 중소규모
    if is_assignment or (has_multi_index_binary and var_count <= 5000):
        return "assignment"

    return "generic_mip"


def _classify_problem(
    problem_name: str,
    variables: List[Dict],
    constraints: List[Dict],
    constraint_features: Dict,
    has_multi_index_binary: bool,
    objective: Dict,
) -> List[str]:
    """
    문제 클래스 분류 (v2.0)

    1단계: 키워드 매칭 (문제 이름 + 제약조건 설명)
    2단계: 구조 기반 추론 (변수/제약 패턴)
    """
    problem_classes = set()
    name_lower = problem_name.lower()

    # 모든 텍스트를 합쳐서 키워드 검색 범위 확대
    all_text = name_lower
    for c in constraints:
        all_text += " " + str(c.get("description", "")).lower()
        all_text += " " + str(c.get("name", "")).lower()
    all_text += " " + str(objective.get("description", "")).lower()

    # 1단계: 키워드 매칭
    for cls_name, keywords in _PROBLEM_CLASS_KEYWORDS.items():
        if any(kw.lower() in all_text for kw in keywords):
            problem_classes.add(cls_name)

    # 2단계: 구조 기반 추론
    # 2-index binary variable → assignment or scheduling 가능성
    if has_multi_index_binary and not problem_classes.intersection({"assignment", "scheduling"}):
        problem_classes.add("assignment")

    # all_different 제약 → permutation
    if constraint_features.get("has_permutation"):
        problem_classes.add("permutation")

    # binary selection 패턴 → subset_selection 가능성
    obj_expr = str(objective.get("expression", "")).lower()
    if "sum" in obj_expr and any(v.get("type", "").lower() == "binary" for v in variables):
        # binary selection + sum objective = subset-like
        if not problem_classes.intersection({"scheduling", "assignment", "routing"}):
            problem_classes.add("subset_selection")

    if not problem_classes:
        problem_classes.add("general_optimization")

    return sorted(problem_classes)


def _parse_int(value) -> int:
    """안전한 정수 변환"""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.replace(",", ""))
        except ValueError:
            return 0
    return 0


# ============================================================
# 시간/비용 추정
# ============================================================

def estimate_time(solver: Dict, variable_count: int) -> List[float]:
    """문제 규모 기반 실행 시간 추정 [min_seconds, max_seconds]"""
    tp = solver.get("time_profile")
    if not tp:
        return [0, 0]

    ref_vars = tp.get("reference_variables", 1000)
    base_time = tp.get("base_time_seconds", [1, 60])
    scaling = tp.get("scaling_model", "linear")
    factor = tp.get("scaling_factor", 1.0)
    max_time = tp.get("max_time_seconds", 3600)

    ratio = max(variable_count, 1) / max(ref_vars, 1)

    if scaling == "sublinear":
        multiplier = (1 + math.log2(max(ratio, 1))) * factor
    elif scaling == "exponential":
        multiplier = (ratio ** factor)
    else:  # linear
        multiplier = ratio * factor

    estimated_min = min(base_time[0] * multiplier, max_time)
    estimated_max = min(base_time[1] * multiplier, max_time)

    return [round(estimated_min, 1), round(estimated_max, 1)]


def estimate_cost(solver: Dict, estimated_time: List[float]) -> List[float]:
    """추정 시간 기반 비용 계산 [min_cost, max_cost] (USD)"""
    cpm = solver.get("cost_per_minute", 0)
    if cpm == 0:
        return [0, 0]
    return [
        round(cpm * estimated_time[0] / 60, 4),
        round(cpm * estimated_time[1] / 60, 4),
    ]


# ============================================================
# Scoring Engine (v2.0 — 정밀화)
# ============================================================

# model_type → 적합한 문제 특성 매핑
_MODEL_TYPE_AFFINITY = {
    "NL": {
        "native_classes": {"permutation", "subset_selection", "TSP", "scheduling", "routing", "resource_allocation"},
        "supports_nonlinear": True,
        "native_bonus_classes": {"permutation", "subset_selection"},  # QUBO 변환 불필요
    },
    "CQM": {
        "native_classes": {"scheduling", "routing", "assignment", "knapsack", "resource_allocation"},
        "supports_nonlinear": False,
        "native_bonus_classes": set(),
    },
    "BQM": {
        "native_classes": {"QUBO", "max_cut", "graph_coloring", "clustering"},
        "supports_nonlinear": False,
        "native_bonus_classes": set(),
    },
    "LP_MIP": {
        "native_classes": {"LP", "MIP", "scheduling", "routing", "assignment", "all"},
        "supports_nonlinear": False,
        "native_bonus_classes": set(),
    },
    "circuit": {
        "native_classes": {"QAOA", "VQE", "simulation"},
        "supports_nonlinear": False,
        "native_bonus_classes": set(),
    },
}


# ── Objective Intent × Solver 적합성 (v3.0) ──────────────────
# rule-based scoring: YAML 수정 없이 유지보수 가능

def _score_objective_intent(solver_model_type: str, intent: Dict) -> tuple:
    """목적함수 의도에 따른 solver 보너스/페널티 (점수, 이유)"""
    goal = intent.get("primary_goal", "minimize_count")
    structure = intent.get("structure", "linear")

    # 목적함수별 solver 적합성 매트릭스
    _OBJECTIVE_SCORES = {
        "minimize_count": {"LP_MIP": 10, "CQM": 5, "NL": 5, "BQM": 3},
        "balance":        {"LP_MIP": 5, "CQM": 10, "NL": 10, "BQM": 3},
        "efficiency":     {"LP_MIP": 10, "CQM": 5, "NL": 5, "BQM": 3},
        "cost":           {"LP_MIP": 8, "CQM": 8, "NL": 5, "BQM": 5},
    }

    scores_map = _OBJECTIVE_SCORES.get(goal, {})
    score = scores_map.get(solver_model_type, 0)

    # 비선형 구조 보너스
    if structure in ("quadratic", "nonlinear") and solver_model_type in ("NL", "CQM"):
        score += 5

    _GOAL_LABELS = {
        "minimize_count": "수량 최소화",
        "balance": "균형 최적화",
        "efficiency": "효율 최적화",
        "cost": "비용 최적화",
    }
    reason = f"{_GOAL_LABELS.get(goal, goal)} 적합" if score >= 8 else ""

    return score, reason


# ── Modeling Pattern × Solver 적합성 (v3.0) ──────────────────

# SP capability: solver별 Set Partitioning 처리 방식
_SP_CAPABILITY = {
    "LP_MIP":  {"supported": True,  "method": "direct_mip",       "strength": 1.0},
    "CQM":     {"supported": True,  "method": "constraint_native", "strength": 0.9},
    "NL":      {"supported": True,  "method": "nonlinear_native",  "strength": 0.8},
    "BQM":     {"supported": True,  "method": "penalty",           "strength": 0.5},
    "circuit":  {"supported": False, "method": None,                "strength": 0.0},
    "analog":   {"supported": False, "method": None,                "strength": 0.0},
}


def _score_modeling_pattern(solver_model_type: str, pattern: str) -> tuple:
    """모델링 패턴에 따른 solver 보너스/페널티 (점수, 이유)"""
    if pattern == "set_partitioning":
        cap = _SP_CAPABILITY.get(solver_model_type, {})
        if not cap.get("supported"):
            return -5, "Set Partitioning 미지원"
        score = int(cap.get("strength", 0) * 10)
        method = cap.get("method", "")
        return score, f"SP {method} 지원" if score >= 8 else ""

    elif pattern == "assignment":
        _scores = {"LP_MIP": 8, "CQM": 5, "BQM": 5, "NL": 5}
        score = _scores.get(solver_model_type, 0)
        return score, "배정 문제 적합" if score >= 8 else ""

    elif pattern == "network_flow":
        _scores = {"LP_MIP": 10, "CQM": 5, "NL": 8}
        score = _scores.get(solver_model_type, 0)
        return score, "네트워크 흐름 적합" if score >= 8 else ""

    return 0, ""


def score_solver(solver: Dict, profile: Dict) -> Dict:
    """
    솔버와 문제 프로파일을 비교하여 점수 산출 (v3.0)

    구조 점수 배분 (최대 120점 → 100점 정규화):
      변수 타입 매칭:      40점
      제약조건 지원:       25점
      문제 클래스 매칭:    20점
      NL 네이티브 보너스:  15점 (model_type 매칭)
      목적함수 의도:       10점 (v3.0)
      모델링 패턴:         10점 (v3.0)
    """
    scores = {
        "structure": 0.0,
        "scale": 0.0,
        "cost": 0.0,
        "speed": 0.0,
    }
    reasons = []
    warnings = []

    var_count = profile.get("variable_count", 0)
    hard_count = profile.get("hard_constraint_count", 0)
    soft_count = profile.get("soft_constraint_count", 0)
    constraint_features = profile.get("constraint_features", {})
    prob_classes = set(profile.get("problem_classes", []))
    solver_model_type = solver.get("model_type", "")

    # ══════════════════════════════════════════════════
    # 1) 구조 적합성 (structure) — v2.0 재설계
    # ══════════════════════════════════════════════════

    # ── 1a) 변수 타입 매칭 (40점) ──
    prob_var_types = set(profile.get("variable_types", []))
    solver_var_types = set(solver.get("supported_variable_types", []))
    has_constraints = profile.get("has_constraints", False)
    supports_constraints = solver.get("supports_constraints", False)

    if prob_var_types and solver_var_types:
        match_ratio = len(prob_var_types & solver_var_types) / len(prob_var_types)
        scores["structure"] += match_ratio * 40
        if match_ratio == 1.0:
            reasons.append("모든 변수 타입 지원")
        elif match_ratio > 0:
            warnings.append(f"일부 변수 타입만 지원: {solver_var_types & prob_var_types}")
        else:
            warnings.append("필요한 변수 타입 미지원")

    # ── 1b) 제약조건 지원 (25점) ──
    if has_constraints:
        if supports_constraints:
            scores["structure"] += 25
            reasons.append("제약조건 네이티브 지원")
        else:
            scores["structure"] += 5
            warnings.append("제약조건을 페널티로 변환 필요")
    else:
        scores["structure"] += 15  # 제약조건 없으면 중립

    # ── 1c) 문제 클래스 매칭 (20점) ──
    solver_classes = set(solver.get("problem_classes", []))
    if prob_classes and solver_classes:
        if "all" in solver_classes:
            scores["structure"] += 20
            reasons.append("모든 문제 유형 지원")
        else:
            class_match = len(prob_classes & solver_classes)
            if class_match > 0:
                scores["structure"] += min(20, class_match * 8)
                reasons.append(f"문제 유형 매칭: {prob_classes & solver_classes}")
            else:
                warnings.append("문제 유형 불일치")

    # ── 1d) model_type 매칭 보너스 (15점, v2.0 신규) ──
    affinity = _MODEL_TYPE_AFFINITY.get(solver_model_type, {})
    native_classes = affinity.get("native_classes", set())
    native_bonus_classes = affinity.get("native_bonus_classes", set())

    # 네이티브 보너스: permutation/subset 등 NL 고유 강점
    native_match = prob_classes & native_bonus_classes
    if native_match:
        scores["structure"] += 15
        reasons.append(f"네이티브 지원: {native_match} (QUBO 변환 불필요)")
    elif prob_classes & native_classes:
        scores["structure"] += 8
    # 비선형 목적함수 보너스
    if profile.get("is_nonlinear_objective") and affinity.get("supports_nonlinear"):
        scores["structure"] += 5
        reasons.append("비선형 목적함수 네이티브 지원")

    # ── 1e) 목적함수 의도 매칭 (10점, v3.0 신규) ──
    objective_intent = profile.get("objective_intent", {})
    obj_score, obj_reason = _score_objective_intent(solver_model_type, objective_intent)
    if obj_score > 0:
        scores["structure"] += obj_score
        if obj_reason:
            reasons.append(obj_reason)

    # ── 1f) 모델링 패턴 매칭 (10점, v3.0 신규) ──
    modeling_pattern = profile.get("modeling_pattern", "generic_mip")
    pat_score, pat_reason = _score_modeling_pattern(solver_model_type, modeling_pattern)
    if pat_score > 0:
        scores["structure"] += pat_score
        if pat_reason:
            reasons.append(pat_reason)
    elif pat_score < 0:
        warnings.append(pat_reason or "모델링 패턴 불일치")

    scores["structure"] = min(100, scores["structure"])

    # ══════════════════════════════════════════════════
    # 2) 규모 적합성 (scale) — v2.0 가우시안 커브
    # ══════════════════════════════════════════════════
    max_vars = solver.get("max_variables", 0)
    max_consts = solver.get("max_constraints", 0)

    if max_vars > 0 and var_count > 0:
        if var_count > max_vars:
            scores["scale"] = 0
            warnings.append(f"변수 수 초과 ({var_count:,} > {max_vars:,})")
        else:
            # 가우시안 커브: sweet spot = 0.01~0.3 (max_vars 대비 1~30%)
            utilization = var_count / max_vars
            if utilization > 0:
                log_util = math.log10(utilization)
                # 중심=-1.5 (≈3%), sigma=1.2 → 0.001~0.5에서 높은 점수
                gauss = math.exp(-((log_util + 1.5) ** 2) / (2 * 1.2 ** 2))
                scores["scale"] = round(gauss * 100, 1)
            else:
                scores["scale"] = 20

            if utilization < 0.0001:
                warnings.append(f"솔버 용량 대비 문제가 매우 작음 ({var_count:,} / {max_vars:,})")
            elif utilization > 0.8:
                warnings.append(f"솔버 용량에 근접 ({var_count:,} / {max_vars:,})")
            elif scores["scale"] >= 70:
                reasons.append(f"적절한 규모 ({var_count:,} / {max_vars:,})")
    elif var_count == 0:
        scores["scale"] = 50  # 변수 수 미확인 시 중립

    # 제약조건 용량 체크
    if has_constraints and profile.get("constraint_count", 0) > 0 and max_consts == 0:
        scores["scale"] = max(0, scores["scale"] - 20)
        warnings.append("제약조건 처리 불가")

    # ══════════════════════════════════════════════════
    # 3) 비용 점수 (cost)
    # ══════════════════════════════════════════════════
    est_time = estimate_time(solver, var_count)
    est_cost = estimate_cost(solver, est_time)
    avg_cost = (est_cost[0] + est_cost[1]) / 2

    if avg_cost == 0:
        scores["cost"] = 100
        reasons.append("무료")
    elif avg_cost <= 0.01:
        scores["cost"] = 90
        reasons.append(f"예상 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")
    elif avg_cost <= 0.10:
        scores["cost"] = 75
        reasons.append(f"예상 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")
    elif avg_cost <= 0.50:
        scores["cost"] = 60
        reasons.append(f"예상 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")
    elif avg_cost <= 2.00:
        scores["cost"] = 40
        warnings.append(f"예상 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")
    elif avg_cost <= 10.00:
        scores["cost"] = 20
        warnings.append(f"높은 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")
    else:
        scores["cost"] = 5
        warnings.append(f"매우 높은 비용: ${est_cost[0]:.2f}~${est_cost[1]:.2f}")

    # ══════════════════════════════════════════════════
    # 4) 속도 점수 (speed)
    # ══════════════════════════════════════════════════
    max_time = est_time[1] if est_time[1] > 0 else 9999
    if max_time <= 0.5:
        scores["speed"] = 100
        reasons.append("밀리초 단위 초고속")
    elif max_time <= 5:
        scores["speed"] = 90
        reasons.append(f"예상 시간: {est_time[0]}~{est_time[1]}초")
    elif max_time <= 30:
        scores["speed"] = 80
        reasons.append(f"예상 시간: {est_time[0]}~{est_time[1]}초")
    elif max_time <= 120:
        scores["speed"] = 60
        reasons.append(f"예상 시간: {est_time[0]:.0f}~{est_time[1]:.0f}초")
    elif max_time <= 600:
        scores["speed"] = 40
        warnings.append(f"예상 시간: {est_time[0]/60:.1f}~{est_time[1]/60:.1f}분")
    elif max_time <= 3600:
        scores["speed"] = 20
        warnings.append(f"장시간 소요: 최대 {est_time[1]/60:.0f}분")
    else:
        scores["speed"] = 5
        warnings.append(f"매우 장시간: 최대 {est_time[1]/60:.0f}분")

    # ══════════════════════════════════════════════════
    # 5) 해 품질 보너스/페널티 (v2.0 재조정)
    # ══════════════════════════════════════════════════
    guarantee = solver.get("solution_guarantee", "unknown")
    is_large = var_count > 10000

    if guarantee == "exact":
        if is_large:
            # 대규모 문제: exact 솔버도 시간 내 최적해 못 찾을 수 있음
            reasons.append("최적해 보장 (대규모 문제: 시간 제한 내 근사해 가능)")
        else:
            scores["structure"] = min(100, scores["structure"] + 5)
            reasons.append("최적해 보장")
    elif guarantee == "heuristic":
        scores["structure"] = max(0, scores["structure"] - 5)

    # ══════════════════════════════════════════════════
    # 6) 제약조건 복잡도 보너스/페널티 (v2.0 신규)
    # ══════════════════════════════════════════════════
    total_constraints = hard_count + soft_count

    # 제약조건이 많은 문제: constraint-native 솔버 보너스
    if total_constraints >= 10 and supports_constraints:
        bonus = min(10, total_constraints // 5)
        scores["structure"] = min(100, scores["structure"] + bonus)
        if bonus >= 5:
            reasons.append(f"다수 제약조건({total_constraints}개) 네이티브 처리")

    # BQM: 하드 제약 20개 이상이면 페널티 변환 비효율
    if not supports_constraints and hard_count >= 20:
        penalty = min(15, hard_count // 5)
        scores["structure"] = max(0, scores["structure"] - penalty)
        warnings.append(f"하드 제약 {hard_count}개 → 페널티 변환 비효율")

    # 소프트 제약 가중치 최적화: CQM/NL 보너스
    if soft_count >= 3 and supports_constraints:
        scores["structure"] = min(100, scores["structure"] + 5)

    scores["structure"] = min(100, scores["structure"])

    return {
        "scores": scores,
        "reasons": reasons,
        "warnings": warnings,
    }


# ============================================================
# 솔버 추천 (v2.0)
# ============================================================

def recommend_solvers(
    math_model: Dict,
    priority: str = "auto",
    data_facts: Optional[Dict] = None,
    enabled_solver_ids: Optional[List[str]] = None,
) -> Dict:
    """
    수학 모델 기반 솔버 추천 (v2.0)

    v2.0 변경:
    - build_problem_profile에 data_facts 직접 전달
    - 동적 가중치 지원 (문제 규모 기반)
    """
    # 1. Problem Profile 생성 (v2.0: data_facts 연동)
    profile = build_problem_profile(math_model, data_facts=data_facts)

    # 2. 모든 솔버에 대해 점수 계산
    solvers = SolverRegistry.get_all()

    # ★ 활성화된 솔버만 필터링
    if enabled_solver_ids is not None:
        solvers = [s for s in solvers if s.get("id") in enabled_solver_ids]
        if not solvers:
            return {
                "problem_profile": profile,
                "priority": priority,
                "recommendations": [],
                "top_recommendation": None,
                "quantum_candidates": [],
                "classical_candidates": [],
                "warning": "활성화된 솔버가 없습니다. Settings에서 솔버를 활성화해주세요.",
            }

    # v2.0: 문제 규모 기반 동적 가중치 조정
    weights = _get_dynamic_weights(priority, profile)

    scored = []
    for solver in solvers:
        result = score_solver(solver, profile)

        # 가중치 계산
        total = sum(
            result["scores"][k] * weights.get(k, 0)
            for k in result["scores"]
        )

        scored.append({
            "solver_id": solver.get("id", ""),
            "solver_name": solver.get("name", ""),
            "provider": solver.get("provider", ""),
            "category": solver.get("category", ""),
            "description": solver.get("description", ""),
            "model_type": solver.get("model_type", ""),
            "supported_variable_types": solver.get("supported_variable_types", []),
            "supports_constraints": solver.get("supports_constraints", False),
            "max_variables": solver.get("max_variables", 0),
            "max_constraints": solver.get("max_constraints", 0),
            "strengths": solver.get("strengths", []),
            "weaknesses": solver.get("weaknesses", []),
            "estimated_time": estimate_time(solver, profile.get("variable_count", 0)),
            "estimated_cost": estimate_cost(solver, estimate_time(solver, profile.get("variable_count", 0))),
            "typical_time_seconds": solver.get("typical_time_seconds", []),
            "solution_guarantee": solver.get("solution_guarantee", ""),
            "cost_per_minute": solver.get("cost_per_minute", 0),
            "scores": result["scores"],
            "reasons": result["reasons"],
            "warnings": result["warnings"],
            "total_score": round(total, 1),
            "suitability": _classify_suitability(total),
            "time_limit_sec": solver.get("time_profile", {}).get("max_time_seconds", 120),
        })

    # 3. 점수 순 정렬
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    # 4. 결과 구성
    return {
        "problem_profile": profile,
        "priority": priority,
        "weights_used": weights,
        "recommendations": scored,
        "top_recommendation": scored[0] if scored else None,
        "quantum_candidates": [s for s in scored if "quantum" in s.get("category", "")],
        "classical_candidates": [s for s in scored if "classical" in s.get("category", "")],
    }


def _get_dynamic_weights(priority: str, profile: Dict) -> Dict[str, float]:
    """
    문제 규모 기반 동적 가중치 조정 (v2.0 신규)

    사용자 지정 priority를 기반으로 하되, 문제 규모에 따라 미세 조정
    """
    base = DEFAULT_WEIGHTS.get(priority, DEFAULT_WEIGHTS["auto"]).copy()
    var_count = profile.get("variable_count", 0)

    if priority != "auto":
        # 사용자가 명시적으로 priority를 지정한 경우 그대로 사용
        return base

    # auto 모드에서만 동적 조정
    if var_count > 100000:
        # 대규모: scale/speed 중시, cost 경시
        base = {"structure": 0.30, "scale": 0.35, "cost": 0.10, "speed": 0.25}
    elif var_count < 100:
        # 소규모: structure 중시, scale 경시
        base = {"structure": 0.50, "scale": 0.10, "cost": 0.15, "speed": 0.25}

    return base


# ============================================================
# 기존 유틸리티 (변경 없음)
# ============================================================

def get_solver_time_limit(solver_id: str, db=None) -> int:
    """
    솔버별 실행 time limit 조회.
    우선순위: DB 설정값 > YAML max_time_seconds > 하드코딩 fallback(120s)
    """
    # 1. DB 설정값 (관리자 오버라이드)
    if db is not None:
        try:
            from core import models
            row = db.query(models.SolverSettingDB).filter_by(solver_id=solver_id).first()
            if row and row.time_limit_sec is not None:
                logger.debug(f"time_limit for {solver_id}: DB={row.time_limit_sec}s")
                return row.time_limit_sec
        except Exception as e:
            logger.warning(f"DB time_limit lookup failed ({e}), falling back to YAML")

    # 2. YAML max_time_seconds
    solver = SolverRegistry.get_solver(solver_id)
    if solver:
        yaml_max = solver.get("time_profile", {}).get("max_time_seconds")
        if yaml_max:
            logger.debug(f"time_limit for {solver_id}: YAML={yaml_max}s")
            return int(yaml_max)

    # 3. 하드코딩 fallback
    logger.debug(f"time_limit for {solver_id}: fallback=120s")
    return 120


def _classify_suitability(score: float) -> str:
    """점수를 적합도 등급으로 변환"""
    if score >= 80:
        return "Best Choice"
    elif score >= 65:
        return "Recommended"
    elif score >= 50:
        return "Possible"
    elif score >= 35:
        return "Limited"
    else:
        return "Not Suitable"
