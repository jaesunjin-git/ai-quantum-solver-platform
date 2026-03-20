"""
test_set_partitioning.py ────────────────────────────────────
Set Partitioning 파이프라인 테스트.

1. DutyGenerator: duty 생성 + coverage + 품질
2. SetPartitioningCompiler: SP 모델 컴파일
3. SPResultConverter: 결과 변환
4. E2E: Generator → Compiler → Solve → Convert
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ── Test Data ──────────────────────────────────────────────

def _make_trips(n=20):
    """간단한 테스트 trip 목록 생성"""
    from engine.column_generator import TaskItem as TripInfo
    trips = []
    for i in range(n):
        dep = 360 + i * 40  # 06:00부터 40분 간격
        trips.append(TripInfo(
            id=1000 + i,
            dep_time=dep,
            arr_time=dep + 35,
            duration=35,
            start_location="A",
            end_location="B" if i % 2 == 0 else "A",
            direction="forward" if i % 2 == 0 else "reverse",
        ))
    return trips


def _make_trips_with_night():
    """주간 + 야간 trip 포함"""
    from engine.column_generator import TaskItem as TripInfo
    trips = _make_trips(15)
    # 야간 trip 추가
    for i in range(5):
        dep = 1050 + i * 40  # 17:30부터
        trips.append(TripInfo(
            id=2000 + i,
            dep_time=dep,
            arr_time=dep + 35,
            duration=35,
            start_location="A",
            end_location="B" if i % 2 == 0 else "A",
            direction="forward" if i % 2 == 0 else "reverse",
        ))
    return trips


# ── 1. DutyGenerator ──────────────────────────────────────

class TestDutyGenerator:
    """Duty 생성기 테스트"""

    def test_generate_produces_duties(self):
        """기본 생성 동작"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100, max_tasks=5)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()
        assert len(duties) > 0

    def test_full_coverage(self):
        """모든 trip이 최소 1개 duty에 포함"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips(20)
        config = GeneratorConfig(beam_width=20, max_columns_target=500, max_tasks=8)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        covered = set()
        for d in duties:
            covered.update(d.trips)
        assert covered == {t.id for t in trips}

    def test_feasibility_checks(self):
        """생성된 duty가 driving/work 제한 준수"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips(15)
        config = GeneratorConfig(
            max_active_time=360,
            max_span_time=660,
            beam_width=10,
            max_columns_target=200,
        )
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        for d in duties:
            assert d.driving_minutes <= config.max_active_time
            if not d.column_type != "day":
                assert d.work_minutes <= config.max_span_time

    def test_source_metadata(self):
        """duty source 메타데이터 존재"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        sources = {d.source for d in duties}
        assert "beam" in sources or "fallback" in sources or "greedy" in sources

    def test_dominance_removes_duplicates(self):
        """같은 trip set → Pareto dominance로 중복 제거"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=20, max_columns_target=500)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        # 같은 trip set이 여러 번 나오지 않아야 (Pareto 기준 non-dominated만)
        trip_sets = [tuple(sorted(d.trips)) for d in duties]
        from collections import Counter
        duplicates = {k: v for k, v in Counter(trip_sets).items() if v > 1}
        # Pareto non-dominated면 같은 set에 여러 개 있을 수 있지만 제한적
        assert len(duplicates) <= len(duties) * 0.1  # 10% 이하

    def test_night_duty_detection(self):
        """야간 trip → is_night=True"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        trips = _make_trips_with_night()
        config = GeneratorConfig(beam_width=10, max_columns_target=200)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        night_duties = [d for d in duties if d.column_type != "day"]
        # 야간 trip이 있으므로 야간 duty도 있어야
        assert len(night_duties) > 0


# ── 2. SetPartitioningCompiler ────────────────────────────

class TestSPCompiler:
    """SP 컴파일러 테스트"""

    def test_compile_produces_model(self):
        """SP 모델 컴파일 성공"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        from engine.compiler.set_partitioning_compiler import SetPartitioningCompiler

        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        compiler = SetPartitioningCompiler()
        result = compiler.compile({}, {"parameters": {}}, duties=duties)

        assert result.success
        assert result.variable_count > 0
        assert result.constraint_count > 0
        assert result.metadata["model_type"] == "SetPartitioning"

    def test_compile_no_duties_fails(self):
        """duty 없으면 실패"""
        from engine.compiler.set_partitioning_compiler import SetPartitioningCompiler
        compiler = SetPartitioningCompiler()
        result = compiler.compile({}, {"parameters": {}}, duties=[])
        assert not result.success

    def test_solve_feasible(self):
        """SP 모델 풀이 가능"""
        from ortools.sat.python import cp_model
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        from engine.compiler.set_partitioning_compiler import SetPartitioningCompiler

        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()

        compiler = SetPartitioningCompiler()
        result = compiler.compile({}, {"parameters": {}}, duties=duties)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10
        status = solver.solve(result.solver_model)

        assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)


# ── 3. SPResultConverter ──────────────────────────────────

class TestSPResultConverter:
    """결과 변환 테스트"""

    def test_convert_produces_interpretation(self):
        """변환 결과에 필수 필드 존재"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig, FeasibleDuty
        from engine.sp_result_converter import convert_sp_result

        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()
        duty_map = {d.id: d for d in duties}

        # 가짜 solution: 첫 몇 개 duty 선택
        solution = {"z": {str(d.id): 1 for d in duties[:5]}}

        result = convert_sp_result(
            solution=solution,
            duty_map=duty_map,
            trips=trips,
        )

        assert "kpi" in result
        assert "duties" in result
        assert "status" in result
        assert result["kpi"]["active_duties"] == 5

    def test_kpi_fields(self):
        """KPI 필수 필드 검증"""
        from domains.crew.duty_generator import CrewDutyGenerator as DutyGenerator, CrewDutyConfig as GeneratorConfig
        from engine.sp_result_converter import convert_sp_result

        trips = _make_trips(10)
        config = GeneratorConfig(beam_width=10, max_columns_target=100)
        gen = DutyGenerator(trips, config)
        duties = gen.generate()
        duty_map = {d.id: d for d in duties}

        solution = {"z": {str(d.id): 1 for d in duties[:3]}}
        result = convert_sp_result(solution=solution, duty_map=duty_map, trips=trips)

        kpi = result["kpi"]
        assert "active_duties" in kpi
        assert "total_trips" in kpi
        assert "covered_trips" in kpi
        assert "coverage_rate" in kpi
        assert "driving_efficiency" in kpi

    def test_empty_solution(self):
        """빈 solution 처리"""
        from engine.sp_result_converter import convert_sp_result

        result = convert_sp_result(
            solution={"z": {}},
            duty_map={},
            trips=[],
        )
        assert result["kpi"]["active_duties"] == 0


# ── 4. GeneratorConfig ────────────────────────────────────

class TestGeneratorConfig:
    """설정 로딩 테스트"""

    def test_default_values(self):
        from domains.crew.duty_generator import CrewDutyConfig
        cfg = CrewDutyConfig()
        assert cfg.max_active_time == 360
        assert cfg.max_tasks == 10
        assert cfg.beam_width == 50
        assert cfg.night_threshold == 1020  # crew 전용

    def test_from_params(self):
        from domains.crew.duty_generator import CrewDutyConfig
        params = {
            "max_driving_minutes": 400,
            "preparation_minutes_departure": 50,
        }
        cfg = CrewDutyConfig.from_params(params)
        assert cfg.max_active_time == 400
        assert cfg.setup_time_day == 50
