"""
engine/compiler/compile_types.py
────────────────────────────────
컴파일러 결과 타입 — structured issue 모델 + 제약별 통계.

문자열 기반 warning/error 대신 structured 데이터로 컴파일 결과를 전달하여
Gate3, UI, 로그, 테스트에서 일관되게 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CompileIssue:
    """컴파일 중 발생한 개별 이슈."""
    code: str              # DATA_ERROR, CONSTANT_INFEASIBLE, TRUNCATION, FALLBACK, BUDGET_EXHAUSTED
    severity: str          # error, warning, info
    constraint: str        # 제약 이름
    category: str          # hard, soft
    binding: Optional[Dict] = None
    detail: str = ""


@dataclass
class ConstraintCompileStat:
    """제약별 컴파일 통계."""
    name: str
    category: str          # hard, soft
    estimated: int = 0     # 사전 추정 바인딩 수
    allocated: int = 0     # 할당된 budget
    requested: int = 0     # 실제 요청 바인딩 수
    emitted: int = 0       # 실제 생성된 제약 수
    truncated: bool = False
    data_error_count: int = 0
    constant_infeasible_count: int = 0
    constant_tautology_count: int = 0
    method: str = ""       # structured, affine_collector, expression_parser, compact_activation, fast_path


@dataclass
class CompilePolicy:
    """컴파일 정책: strict(운영) / debug(개발).
    환경변수 COMPILE_MODE=strict|debug로 전환. 기본값 strict."""
    mode: str = "strict"

    @property
    def allow_partial_hard(self) -> bool:
        return self.mode == "debug"

    @property
    def fail_on_constant_infeasible(self) -> bool:
        return self.mode == "strict"

    @property
    def strict_data_errors(self) -> bool:
        return self.mode == "strict"


@dataclass
class CompileContext:
    """컴파일 중 사용되는 로컬 컨텍스트. 인스턴스 상태 대신 사용."""
    issues: List[CompileIssue] = field(default_factory=list)
    constraint_stats: List[ConstraintCompileStat] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)       # hard fail 조건
    warnings: List[str] = field(default_factory=list)     # soft 문제

    # 정책 (CompilePolicy에서 파생)
    strict_data_errors: bool = True
    allow_partial_hard: bool = False
    fail_on_constant_infeasible: bool = True
    max_error_count: int = 50

    def add_issue(self, issue: CompileIssue):
        self.issues.append(issue)
        if issue.severity == "error":
            self.errors.append(f"[{issue.code}] {issue.constraint}: {issue.detail}")
        elif issue.severity == "warning":
            self.warnings.append(f"[{issue.code}] {issue.constraint}: {issue.detail}")

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def error_limit_reached(self) -> bool:
        return len(self.errors) >= self.max_error_count

    # ── 집계 ──
    @property
    def hard_truncation_count(self) -> int:
        return sum(1 for s in self.constraint_stats if s.truncated and s.category == "hard")

    @property
    def soft_truncation_count(self) -> int:
        return sum(1 for s in self.constraint_stats if s.truncated and s.category != "hard")

    @property
    def data_error_count(self) -> int:
        return sum(1 for i in self.issues if i.code == "DATA_ERROR")

    @property
    def constant_infeasible_count(self) -> int:
        return sum(1 for i in self.issues if i.code == "CONSTANT_INFEASIBLE")

    @property
    def feasibility_exact(self) -> bool:
        """hard truncation/infeasible/data_error 없음."""
        return (
            self.hard_truncation_count == 0 and
            sum(1 for i in self.issues if i.code == "CONSTANT_INFEASIBLE" and i.category == "hard") == 0 and
            sum(1 for i in self.issues if i.code == "DATA_ERROR" and i.category == "hard") == 0
        )

    @property
    def objective_exact(self) -> bool:
        """soft truncation/objective fallback 없음."""
        return (
            self.soft_truncation_count == 0 and
            sum(1 for i in self.issues if i.code == "FALLBACK" and "objective" in i.detail.lower()) == 0
        )
