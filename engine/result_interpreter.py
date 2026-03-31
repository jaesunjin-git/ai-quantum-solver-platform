"""
engine/result_interpreter.py — Re-export wrapper (하위 호환)

crew scheduling 전용 결과 해석기는 domains/crew/result_interpreter.py로 이동.
이 파일은 기존 import 경로를 유지하기 위한 re-export wrapper.

실제 코드: domains/crew/result_interpreter.py
플랫폼 Base: engine/result_interpreter_base.py
"""

# crew domain interpreter 등록 (import 시 자동)
from domains.crew.result_interpreter import (  # noqa: F401
    RailwayResultInterpreter,
    interpret_result,
    save_artifacts,
    classify_objective,
)

__all__ = [
    "RailwayResultInterpreter",
    "interpret_result",
    "save_artifacts",
    "classify_objective",
]
