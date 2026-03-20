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

import logging
from typing import Any, Dict, Optional, Type

from engine.compiler.base import BaseCompiler

logger = logging.getLogger(__name__)


# ── Registry ─────────────────────────────────────────────────
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

# solver_id → solver_backend 매핑
_SOLVER_BACKEND_MAP = {
    "classical_cpu": "ortools_cp",
    "nvidia_cuopt": "ortools_cp",
    "dwave_hybrid_cqm": "dwave_cqm",
    "dwave_hybrid_bqm": "dwave_bqm",
    "dwave_nl": "dwave_nl",
    "dwave_advantage_qpu": "dwave_bqm",
    "dwave_advantage2_qpu": "dwave_bqm",
}


def get_sp_compiler(solver_id: str) -> BaseCompiler:
    """
    Set Partitioning용 compiler 반환.

    solver_id → backend → (set_partitioning, backend) → Compiler
    """
    return _get_compiler("set_partitioning", solver_id)


def get_compiler_for_pattern(modeling_pattern: str, solver_id: str) -> BaseCompiler:
    """
    modeling_pattern + solver_id로 적합한 compiler 반환.

    Args:
        modeling_pattern: "set_partitioning" | "assignment" | "network_flow" | "generic_mip"
        solver_id: "classical_cpu" | "dwave_hybrid_cqm" | ...

    Returns:
        Compiler instance

    Raises:
        ValueError: 등록되지 않은 조합
    """
    return _get_compiler(modeling_pattern, solver_id)


def _get_compiler(pattern: str, solver_id: str) -> BaseCompiler:
    """registry에서 compiler를 찾아 인스턴스 반환"""
    backend = _SOLVER_BACKEND_MAP.get(solver_id)
    if not backend:
        raise ValueError(f"Unknown solver_id: {solver_id}")

    key = (pattern, backend)
    class_path = _COMPILER_REGISTRY.get(key)

    if not class_path:
        # fallback: generic_mip으로 시도
        fallback_key = ("generic_mip", backend)
        class_path = _COMPILER_REGISTRY.get(fallback_key)
        if class_path:
            logger.warning(f"Compiler registry: ({pattern}, {backend}) not found, "
                          f"using fallback ({fallback_key})")
        else:
            raise ValueError(
                f"No compiler registered for ({pattern}, {backend}). "
                f"Available: {list(_COMPILER_REGISTRY.keys())}"
            )

    # lazy import
    module_path, class_name = class_path.rsplit(".", 1)
    try:
        import importlib
        module = importlib.import_module(module_path)
        compiler_cls = getattr(module, class_name)
        return compiler_cls()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load compiler {class_path}: {e}")


def register_compiler(pattern: str, backend: str, class_path: str):
    """런타임에 compiler 등록 (플러그인 확장용)"""
    _COMPILER_REGISTRY[(pattern, backend)] = class_path
    logger.info(f"Compiler registered: ({pattern}, {backend}) → {class_path}")
