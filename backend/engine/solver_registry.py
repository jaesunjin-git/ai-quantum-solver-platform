# ============================================================
# engine/solver_registry.py — v1.0
# ============================================================
# Solver Registry: YAML 파일에서 솔버 정보를 로드하고
# Problem Profile 기반으로 적합한 솔버를 추천
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

# tier → 점수 매핑
TIER_SCORES = {
    "free": 100, "low": 80, "medium": 60, "high": 40, "very_high": 20,
    "very_fast": 100, "fast": 80, "medium": 60, "variable": 50, "slow": 30,
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


def build_problem_profile(math_model: Dict) -> Dict:
    """수학 모델에서 Problem Profile 추출"""

    # 변수 정보
    variables = math_model.get("variables", math_model.get("decision_variables", []))
    var_types = set()
    total_vars = 0

    for v in variables:
        vtype = v.get("type", "binary").lower()
        var_types.add(vtype)
        # 인덱스 기반 변수 수 추정
        indices = v.get("indices", [])
        if indices:
            # 추후 data_facts에서 정확한 크기 가져올 수 있음
            total_vars += 1
        else:
            total_vars += 1

    # metadata에서 추정값 가져오기
    metadata = math_model.get("metadata", math_model.get("estimation", {}))
    estimated_vars = metadata.get("estimated_variable_count", total_vars)
    if isinstance(estimated_vars, str):
        try:
            estimated_vars = int(estimated_vars.replace(",", ""))
        except ValueError:
            estimated_vars = total_vars

    estimated_constraints = metadata.get("estimated_constraint_count", 0)
    if isinstance(estimated_constraints, str):
        try:
            estimated_constraints = int(estimated_constraints.replace(",", ""))
        except ValueError:
            estimated_constraints = 0

    # 제약조건 분석
    constraints = math_model.get("constraints", [])
    hard_constraints = [c for c in constraints if str(c.get("category", "")).lower() in ("hard", "필수", "")]
    soft_constraints = [c for c in constraints if str(c.get("category", "")).lower() in ("soft", "선호")]
    has_constraints = len(constraints) > 0

    # 문제 도메인
    domain = math_model.get("domain", "general")
    problem_name = math_model.get("problem_name", "")

    # 문제 클래스 추정
    problem_classes = []
    name_lower = problem_name.lower()
    if any(kw in name_lower for kw in ["스케줄", "schedule", "scheduling"]):
        problem_classes.append("scheduling")
    if any(kw in name_lower for kw in ["라우팅", "routing", "경로", "배송"]):
        problem_classes.append("routing")
    if any(kw in name_lower for kw in ["배정", "assign", "할당"]):
        problem_classes.append("assignment")
    if any(kw in name_lower for kw in ["시뮬레이션", "simulation"]):
        problem_classes.append("simulation")
    if not problem_classes:
        problem_classes.append("general_optimization")

    return {
        "variable_count": estimated_vars,
        "constraint_count": estimated_constraints,
        "variable_types": list(var_types) if var_types else ["binary"],
        "has_constraints": has_constraints,
        "hard_constraint_count": len(hard_constraints),
        "soft_constraint_count": len(soft_constraints),
        "domain": domain,
        "problem_name": problem_name,
        "problem_classes": problem_classes,
        "variable_types_used": metadata.get("variable_types_used", list(var_types)),
    }

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

def score_solver(solver: Dict, profile: Dict) -> Dict:
    """솔버와 문제 프로파일을 비교하여 점수 산출 (객관적 데이터 기반)"""
    scores = {
        "structure": 0.0,
        "scale": 0.0,
        "cost": 0.0,
        "speed": 0.0,
    }
    reasons = []
    warnings = []

    # ── 1) 구조 적합성 (structure) ──
    prob_var_types = set(profile.get("variable_types", []))
    solver_var_types = set(solver.get("supported_variable_types", []))
    has_constraints = profile.get("has_constraints", False)
    supports_constraints = solver.get("supports_constraints", False)

    # 변수 타입 매칭
    if prob_var_types and solver_var_types:
        match_ratio = len(prob_var_types & solver_var_types) / len(prob_var_types)
        scores["structure"] += match_ratio * 50
        if match_ratio == 1.0:
            reasons.append("모든 변수 타입 지원")
        elif match_ratio > 0:
            warnings.append(f"일부 변수 타입만 지원: {solver_var_types & prob_var_types}")
        else:
            warnings.append("필요한 변수 타입 미지원")

    # 제약조건 지원
    if has_constraints:
        if supports_constraints:
            scores["structure"] += 30
            reasons.append("제약조건 네이티브 지원")
        else:
            scores["structure"] += 5
            warnings.append("제약조건을 페널티로 변환 필요")
    else:
        scores["structure"] += 20

    # 문제 클래스 매칭
    prob_classes = set(profile.get("problem_classes", []))
    solver_classes = set(solver.get("problem_classes", []))
    if prob_classes and solver_classes:
        if "all" in solver_classes:
            scores["structure"] += 20
            reasons.append("모든 문제 유형 지원")
        else:
            class_match = len(prob_classes & solver_classes)
            if class_match > 0:
                scores["structure"] += min(20, class_match * 10)
                reasons.append(f"문제 유형 매칭: {prob_classes & solver_classes}")
            else:
                warnings.append("문제 유형 불일치")

    scores["structure"] = min(100, scores["structure"])

    # ── 2) 규모 적합성 (scale) ──
    var_count = profile.get("variable_count", 0)
    const_count = profile.get("constraint_count", 0)
    max_vars = solver.get("max_variables", 0)
    max_consts = solver.get("max_constraints", 0)

    if max_vars > 0 and var_count > 0:
        if var_count <= max_vars:
            utilization = var_count / max_vars
            if utilization < 0.001:
                scores["scale"] = 40
                warnings.append(f"솔버 용량 대비 문제가 매우 작음 ({var_count:,} / {max_vars:,})")
            elif utilization < 0.01:
                scores["scale"] = 60
            elif utilization < 0.5:
                scores["scale"] = 90
                reasons.append(f"적절한 규모 ({var_count:,} / {max_vars:,})")
            else:
                scores["scale"] = 70
                warnings.append("솔버 용량에 근접")
        else:
            scores["scale"] = 0
            warnings.append(f"변수 수 초과 ({var_count:,} > {max_vars:,})")

    if has_constraints and const_count > 0 and max_consts == 0:
        scores["scale"] = max(0, scores["scale"] - 20)
        warnings.append("제약조건 처리 불가")

    # ── 3) 비용 점수 (cost) — 추정 비용 기반 ──
    var_count = profile.get("variable_count", 0)
    est_time = estimate_time(solver, var_count)
    est_cost = estimate_cost(solver, est_time)
    avg_cost = (est_cost[0] + est_cost[1]) / 2  # 중간값

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

    # ── 4) 속도 점수 (speed) — 추정 시간 기반 ──
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

    # ── 5) 해 품질 보너스/페널티 ──
    guarantee = solver.get("solution_guarantee", "unknown")
    if guarantee == "exact":
        scores["structure"] = min(100, scores["structure"] + 10)
        reasons.append("최적해 보장")
    elif guarantee == "heuristic":
        scores["structure"] = max(0, scores["structure"] - 5)

    return {
        "scores": scores,
        "reasons": reasons,
        "warnings": warnings,
    }

def recommend_solvers(
    math_model: Dict,
    priority: str = "auto",
    data_facts: Optional[Dict] = None,
    enabled_solver_ids: Optional[List[str]] = None,
) -> Dict:
    """
    수학 모델 기반 솔버 추천

    Args:
        math_model: 확정된 수학 모델 JSON
        priority: "auto" | "accuracy" | "speed" | "cost"
        data_facts: 코드로 계산된 팩트 데이터 (변수 수 보정용)

    Returns:
        추천 결과 딕셔너리
    """
    # 1. Problem Profile 생성
    profile = build_problem_profile(math_model)

    # data_facts로 변수 수 보정
    if data_facts:
        unique_counts = data_facts.get("unique_counts", {})
        if unique_counts:
            profile["data_facts_available"] = True

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

    weights = DEFAULT_WEIGHTS.get(priority, DEFAULT_WEIGHTS["auto"])

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
        })

    # 3. 점수 순 정렬
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    # 4. 결과 구성
    return {
        "problem_profile": profile,
        "priority": priority,
        "recommendations": scored,
        "top_recommendation": scored[0] if scored else None,
        "quantum_candidates": [s for s in scored if "quantum" in s.get("category", "")],
        "classical_candidates": [s for s in scored if "classical" in s.get("category", "")],
    }


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