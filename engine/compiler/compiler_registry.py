"""
compiler_registry.py ──────────────────────────────────────
Compiler Registry — (problem_type, solver_type) → Compiler 매핑.

solver_id 기반 if 분기 대신 registry 패턴으로 compiler 선택.
새 solver/문제 유형 추가 시 registry에 등록만 하면 됨.

구조:
  (modeling_pattern, solver_type) → CompilerClass
  예: ("set_partitioning", "ortools_cp") → SetPartitioningCompiler
      ("set_partitioning", "dwave_cqm") → CQMCompiler
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

from engine.compiler.base import BaseCompiler

logger = logging.getLogger(__name__)


# ── Solver Info: 단일 진실 공급원 ────────────────────────────

@dataclass
class SolverInfo:
    """solver_id별 메타 정보 — solver_pipeline 등에서 참조

    has_sp_backend: 이 solver의 backend에 SP compiler가 등록되어 있는가
    (problem_type이 Column Generation을 사용하는지와는 독립된 축)
    """
    solver_id: str
    backend: str
    has_sp_backend: bool = False     # SP compiler 등록 여부
    display_name: str = ""


_SOLVER_INFO: Dict[str, SolverInfo] = {
    "classical_cpu": SolverInfo("classical_cpu", "ortools_cp", has_sp_backend=True, display_name="CP-SAT"),
    "nvidia_cuopt": SolverInfo("nvidia_cuopt", "ortools_cp", has_sp_backend=True, display_name="cuOpt"),
    "dwave_hybrid_cqm": SolverInfo("dwave_hybrid_cqm", "dwave_cqm", has_sp_backend=False, display_name="D-Wave CQM"),
    "dwave_hybrid_bqm": SolverInfo("dwave_hybrid_bqm", "dwave_bqm", has_sp_backend=False, display_name="D-Wave BQM"),
    "dwave_nl": SolverInfo("dwave_nl", "dwave_nl", has_sp_backend=False, display_name="D-Wave NL"),
    "dwave_advantage_qpu": SolverInfo("dwave_advantage_qpu", "dwave_bqm", has_sp_backend=False, display_name="D-Wave Advantage QPU"),
    "dwave_advantage2_qpu": SolverInfo("dwave_advantage2_qpu", "dwave_bqm", has_sp_backend=False, display_name="D-Wave Advantage2 QPU"),
}

# ── Problem Type: Column Generation 사용 여부 ────────────────
# SP 경로는 두 조건이 모두 충족될 때만 사용:
#   1. solver가 SP backend를 가짐 (has_sp_backend)
#   2. problem_type이 Column Generation을 사용 (uses_column_generation)
_COLUMN_GEN_PROBLEM_TYPES = {
    "crew_scheduling",
    # 향후:
    # "vehicle_routing",  # Column Generation 기반 VRP
}


def get_solver_backend(solver_id: str) -> str:
    """solver_id → backend 변환"""
    info = _SOLVER_INFO.get(solver_id)
    if not info:
        raise ValueError(f"Unknown solver_id: {solver_id}")
    return info.backend


def supports_set_partitioning(solver_id: str, problem_type: str = None) -> bool:
    """SP 경로 사용 가능 여부: solver × problem_type 2축 판단.

    두 조건 모두 충족 시 SP 경로:
      1. solver에 SP backend가 등록되어 있음
      2. problem_type이 Column Generation을 사용함

    problem_type이 None이면 solver 축만 확인 (하위 호환).
    """
    info = _SOLVER_INFO.get(solver_id)
    if not info or not info.has_sp_backend:
        return False

    if problem_type is None:
        # 하위 호환: problem_type 미제공 시 solver 축만 확인
        return info.has_sp_backend

    return problem_type in _COLUMN_GEN_PROBLEM_TYPES


# ── Compiler Registry ─────────────────────────────────────────
# (modeling_pattern, solver_backend) → Compiler class
# lazy import로 순환 import 방지

_COMPILER_REGISTRY: Dict[tuple, str] = {
    # Set Partitioning
    ("set_partitioning", "ortools_cp"): "engine.compiler.set_partitioning_compiler.SetPartitioningCompiler",
    ("set_partitioning", "dwave_cqm"): "engine.compiler.cqm_compiler.CQMCompiler",

    # Assignment (기존 I×J)
    ("assignment", "ortools_cp"): "engine.compiler.ortools_compiler.ORToolsCompiler",
    ("assignment", "dwave_cqm"): "engine.compiler.dwave_cqm_compiler.DWaveCQMCompiler",
    ("assignment", "dwave_bqm"): "engine.compiler.dwave_bqm_compiler.DWaveBQMCompiler",
    ("assignment", "dwave_nl"): "engine.compiler.dwave_nl_compiler.DWaveNLCompiler",

    # Generic MIP
    ("generic_mip", "ortools_cp"): "engine.compiler.ortools_compiler.ORToolsCompiler",
}


def get_sp_compiler(solver_id: str) -> BaseCompiler:
    """Set Partitioning용 compiler 반환."""
    return _get_compiler("set_partitioning", solver_id)


def get_compiler_for_pattern(modeling_pattern: str, solver_id: str) -> BaseCompiler:
    """modeling_pattern + solver_id로 적합한 compiler 반환."""
    return _get_compiler(modeling_pattern, solver_id)


def _get_compiler(pattern: str, solver_id: str) -> BaseCompiler:
    """registry에서 compiler를 찾아 인스턴스 반환. fallback 없음 — 명시적 등록 강제."""
    backend = get_solver_backend(solver_id)

    key = (pattern, backend)
    class_path = _COMPILER_REGISTRY.get(key)

    if not class_path:
        raise ValueError(
            f"No compiler registered for ({pattern}, {backend}). "
            f"Register it in _COMPILER_REGISTRY or use register_compiler(). "
            f"Available: {list(_COMPILER_REGISTRY.keys())}"
        )

    # lazy import
    module_path, class_name = class_path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        compiler_cls = getattr(module, class_name)
        return compiler_cls()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load compiler {class_path}: {e}")


def register_compiler(
    pattern: str,
    backend: str,
    class_path: str,
    overwrite: bool = False,
):
    """런타임에 compiler 등록 (플러그인 확장용). 기존 등록 덮어쓰기 방지."""
    key = (pattern, backend)
    if key in _COMPILER_REGISTRY and not overwrite:
        raise ValueError(
            f"Compiler already registered for {key}: "
            f"{_COMPILER_REGISTRY[key]}. Use overwrite=True to replace."
        )
    _COMPILER_REGISTRY[key] = class_path
    logger.info(f"Compiler registered: {key} → {class_path}")
