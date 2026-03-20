"""
engine/compiler/__init__.py ────────────────────────────────
Compiler Facade — 통합 진입점.

외부에서는 get_compiler_unified() 하나만 사용.
내부적으로 SP 경로(registry)와 legacy 경로(COMPILER_MAP)를 분기.
"""

from .base import BaseCompiler, DataBinder, CompileResult
from .ortools_compiler import ORToolsCompiler
from .dwave_cqm_compiler import DWaveCQMCompiler
from .dwave_bqm_compiler import DWaveBQMCompiler
from .dwave_nl_compiler import DWaveNLCompiler

# ── Legacy Compiler Map (I×J 경로, 점진 전환 대상) ──────────
# 하위 호환: COMPILER_MAP 이름으로도 접근 가능
COMPILER_MAP = _LEGACY_COMPILER_MAP = {
    "classical_cpu": ORToolsCompiler,
    "nvidia_cuopt": ORToolsCompiler,
    "dwave_hybrid_cqm": DWaveCQMCompiler,
    "dwave_hybrid_bqm": DWaveBQMCompiler,
    "dwave_advantage_qpu": DWaveBQMCompiler,
    "dwave_advantage2_qpu": DWaveBQMCompiler,
    "dwave_nl": DWaveNLCompiler,
}


def get_compiler(solver_id: str) -> BaseCompiler:
    """Legacy 진입점 (하위 호환). 새 코드는 get_compiler_unified() 사용."""
    cls = _LEGACY_COMPILER_MAP.get(solver_id)
    if cls is None:
        raise ValueError(f"No compiler available for solver: {solver_id}")
    return cls()


def get_compiler_unified(
    solver_id: str,
    modeling_pattern: str = "generic_mip",
    use_sp: bool = False,
) -> BaseCompiler:
    """
    통합 Compiler 진입점 (Facade).

    SP 경로: compiler_registry 사용 (problem_type + solver_id)
    Legacy: COMPILER_MAP 사용 (solver_id만)

    Args:
        solver_id: "classical_cpu", "dwave_hybrid_cqm", ...
        modeling_pattern: "set_partitioning", "assignment", "generic_mip"
        use_sp: True이면 SP registry, False이면 legacy
    """
    if use_sp:
        from .compiler_registry import get_sp_compiler
        return get_sp_compiler(solver_id)
    else:
        return get_compiler(solver_id)
