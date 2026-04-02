"""
tests/test_depot_separation.py
거점(Depot) 분리 — 데이터 모델, 호환성 헬퍼, Generator pruning 검증.
"""

from __future__ import annotations

import os
import csv
import tempfile
import pytest

from engine.column_generator import (
    TaskItem,
    FeasibleColumn,
    BaseColumnConfig,
    BaseColumnGenerator,
    _BeamState,
    is_depot_compatible,
    load_tasks_from_csv,
    resolve_task_depots,
)


# ════════════════════════════════════════════════════════════════
# 1. is_depot_compatible 헬퍼
# ════════════════════════════════════════════════════════════════

class TestIsDepotCompatible:
    """거점 호환성 헬퍼 함수 검증"""

    def test_both_empty_returns_empty(self):
        """둘 다 빈 set → 빈 set (거점 정책 미적용)"""
        result = is_depot_compatible(frozenset(), frozenset())
        assert result == frozenset()

    def test_task_empty_returns_state(self):
        """task wildcard → state 유지"""
        state = frozenset({"노포"})
        result = is_depot_compatible(frozenset(), state)
        assert result == state

    def test_state_empty_returns_task(self):
        """state 미확정 → task에서 초기화"""
        task = frozenset({"노포", "신평"})
        result = is_depot_compatible(task, frozenset())
        assert result == task

    def test_intersection(self):
        """교집합 계산"""
        task = frozenset({"노포", "신평"})
        state = frozenset({"노포"})
        result = is_depot_compatible(task, state)
        assert result == frozenset({"노포"})

    def test_no_intersection(self):
        """교집합 없음"""
        task = frozenset({"신평"})
        state = frozenset({"노포"})
        result = is_depot_compatible(task, state)
        assert result == frozenset()

    def test_wildcard_task_preserves_state(self):
        """wildcard task(빈 set)는 state를 변경하지 않음"""
        state = frozenset({"노포", "신평"})
        result = is_depot_compatible(frozenset(), state)
        assert result == state


# ════════════════════════════════════════════════════════════════
# 2. 데이터 모델
# ════════════════════════════════════════════════════════════════

class TestDataModel:
    """TaskItem.allowed_depots, FeasibleColumn.start_depot/end_depot"""

    def test_task_item_default_wildcard(self):
        """기본값: 빈 frozenset (wildcard)"""
        task = TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                       start_location="대저", end_location="노포")
        assert task.allowed_depots == frozenset()

    def test_task_item_with_depots(self):
        task = TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                       start_location="대저", end_location="노포",
                       allowed_depots=frozenset({"노포"}))
        assert task.allowed_depots == frozenset({"노포"})

    def test_column_default_empty_depot(self):
        col = FeasibleColumn(id=1, trips=[1, 2])
        assert col.start_depot == ""
        assert col.end_depot == ""

    def test_column_with_depot(self):
        col = FeasibleColumn(id=1, trips=[1, 2], start_depot="노포", end_depot="노포")
        assert col.start_depot == "노포"

    def test_beam_state_default_depots(self):
        state = _BeamState(
            trips=[1], last_arr_time=450, last_end_location="대저",
            total_driving=50, first_dep_time=400,
        )
        assert state.current_depots == frozenset()

    def test_beam_state_with_depots(self):
        state = _BeamState(
            trips=[1], last_arr_time=450, last_end_location="대저",
            total_driving=50, first_dep_time=400,
            current_depots=frozenset({"노포"}),
        )
        assert state.current_depots == frozenset({"노포"})


# ════════════════════════════════════════════════════════════════
# 3. Config 모델
# ════════════════════════════════════════════════════════════════

class TestConfig:
    """BaseColumnConfig depot 설정"""

    def test_default_policy_multi(self):
        """기본 정책: multi (자유)"""
        cfg = BaseColumnConfig()
        assert cfg.depot_policy_type == "multi"
        assert not cfg.depot_policy_active

    def test_single_policy(self):
        cfg = BaseColumnConfig()
        cfg.depot_policy = {"type": "single", "max_depot_changes": 0}
        assert cfg.depot_policy_type == "single"
        assert cfg.depot_policy_active

    def test_depot_policy_not_contain_depot_names(self):
        """config에 거점 이름(데이터)이 포함되면 안 됨"""
        cfg = BaseColumnConfig()
        # depots 필드가 config에 없어야 함 (데이터이므로)
        assert not hasattr(cfg, 'depots'), "depots field should not be in config"

    def test_depot_policy_defaults(self):
        """depot_policy 기본값 확인"""
        cfg = BaseColumnConfig()
        assert cfg.depot_policy == {"type": "multi"}
        assert not cfg.depot_policy_active


# ════════════════════════════════════════════════════════════════
# 4. 책임 분리: Data Layer (CSV 읽기) + Problem Layer (depot 결정)
# ════════════════════════════════════════════════════════════════

class TestDataLayerCsvLoader:
    """Data Layer: load_tasks_from_csv는 raw 데이터만 읽음"""

    def _write_csv(self, rows, headers=None):
        if headers is None:
            headers = ["trip_id", "trip_dep_time", "trip_arr_time",
                       "trip_duration", "dep_station", "arr_station", "direction"]
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def test_loader_does_not_set_allowed_depots(self):
        """loader는 allowed_depots를 결정하지 않음"""
        path = self._write_csv([{
            "trip_id": "1", "trip_dep_time": "400", "trip_arr_time": "450",
            "trip_duration": "50", "dep_station": "대저", "arr_station": "노포",
            "direction": "up",
        }])
        try:
            tasks = load_tasks_from_csv(path)
            assert tasks[0].allowed_depots == frozenset()  # 미결정
            assert tasks[0].raw_depot == ""
        finally:
            os.unlink(path)

    def test_loader_reads_raw_depot(self):
        """CSV에 depot 컬럼 있으면 raw_depot에 저장"""
        headers = ["trip_id", "trip_dep_time", "trip_arr_time",
                   "trip_duration", "dep_station", "arr_station", "direction", "depot"]
        path = self._write_csv([{
            "trip_id": "1", "trip_dep_time": "400", "trip_arr_time": "450",
            "trip_duration": "50", "dep_station": "대저", "arr_station": "노포",
            "direction": "up", "depot": "신평",
        }], headers=headers)
        try:
            tasks = load_tasks_from_csv(path)
            assert tasks[0].raw_depot == "신평"
            assert tasks[0].allowed_depots == frozenset()  # loader는 결정 안 함
        finally:
            os.unlink(path)


class TestProblemLayerResolve:
    """Problem Layer: resolve_task_depots가 allowed_depots의 유일한 진실 공급원"""

    def test_no_source_wildcard(self):
        """raw_depot 없고 params도 없으면 wildcard"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="대저", end_location="노포")]
        resolve_task_depots(tasks)
        assert tasks[0].allowed_depots == frozenset()

    def test_raw_depot_priority(self):
        """raw_depot(CSV) > params 매핑"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="대저", end_location="노포",
                         raw_depot="신평")]
        params = {"depots": {"노포": {"stations": ["대저", "노포"]}}}
        resolve_task_depots(tasks, params)
        assert tasks[0].allowed_depots == frozenset({"신평"})

    def test_params_depot_mapping(self):
        """params depots 맵으로 역 기반 매핑"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="대저", end_location="노포")]
        params = {"depots": {"노포": {"stations": ["대저", "노포"]}}}
        resolve_task_depots(tasks, params)
        assert tasks[0].allowed_depots == frozenset({"노포"})

    def test_params_multi_depot_intersection(self):
        """시작/종료 역의 거점 교집합 우선"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="대저", end_location="대저")]
        params = {"depots": {
            "노포": {"stations": ["대저", "노포"]},
            "신평": {"stations": ["대저", "신평"]},  # 대저가 양쪽 거점
        }}
        resolve_task_depots(tasks, params)
        # 시작=대저(노포,신평), 끝=대저(노포,신평) → 교집합 {"노포","신평"}
        assert tasks[0].allowed_depots == frozenset({"노포", "신평"})

    def test_params_cross_depot_union(self):
        """시작/종료 역의 거점이 다르면 합집합"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="신평", end_location="노포")]
        params = {"depots": {
            "노포": {"stations": ["노포"]},
            "신평": {"stations": ["신평"]},
        }}
        resolve_task_depots(tasks, params)
        assert tasks[0].allowed_depots == frozenset({"노포", "신평"})

    def test_unmapped_station_wildcard(self):
        """매핑에 없는 역은 wildcard"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="미지역", end_location="미지역2")]
        params = {"depots": {"노포": {"stations": ["대저"]}}}
        resolve_task_depots(tasks, params)
        assert tasks[0].allowed_depots == frozenset()

    def test_csv_multi_depot_raw(self):
        """raw_depot에 쉼표 구분 다중 거점"""
        tasks = [TaskItem(id=1, dep_time=400, arr_time=450, duration=50,
                         start_location="대저", end_location="노포",
                         raw_depot="노포,신평")]
        resolve_task_depots(tasks)
        assert tasks[0].allowed_depots == frozenset({"노포", "신평"})


# ════════════════════════════════════════════════════════════════
# 5. Generator — depot pruning (통합 테스트)
# ════════════════════════════════════════════════════════════════

class TestGeneratorDepotPruning:
    """거점 정책 활성 시 Generator가 depot를 분리하는지 검증"""

    def _make_tasks(self):
        """노포 2개 + 신평 2개 (시간대 겹침)"""
        return [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="대저", end_location="노포",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="노포", end_location="대저",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=3, dep_time=410, arr_time=445, duration=35,
                    start_location="신평", end_location="신평",
                    allowed_depots=frozenset({"신평"})),
            TaskItem(id=4, dep_time=450, arr_time=480, duration=30,
                    start_location="신평", end_location="신평",
                    allowed_depots=frozenset({"신평"})),
        ]

    def test_multi_policy_no_pruning(self):
        """multi 정책에서는 depot 제약 없이 생성"""
        tasks = self._make_tasks()
        cfg = BaseColumnConfig()
        cfg.depot_policy = {"type": "multi"}
        cfg.max_tasks = 4
        cfg.max_gap = 60
        gen = BaseColumnGenerator(tasks, cfg)
        columns = gen.generate()
        assert len(columns) > 0

    def test_single_policy_no_cross_depot_column(self):
        """single 정책에서는 노포+신평 혼합 column이 없어야 함"""
        tasks = self._make_tasks()
        cfg = BaseColumnConfig()
        cfg.depot_policy = {"type": "single", "max_depot_changes": 0}
        cfg.max_tasks = 4
        cfg.max_gap = 60
        gen = BaseColumnGenerator(tasks, cfg)
        columns = gen.generate()

        for col in columns:
            trip_depots = set()
            for tid in col.trips:
                task = next(t for t in tasks if t.id == tid)
                if task.allowed_depots:
                    trip_depots.update(task.allowed_depots)
            # 한 column에 노포+신평이 동시에 있으면 안 됨
            if "노포" in trip_depots and "신평" in trip_depots:
                pytest.fail(
                    f"Cross-depot column found: col {col.id}, "
                    f"trips={col.trips}, depots={trip_depots}"
                )

    def test_single_policy_assigns_depot_to_column(self):
        """single 정책에서 column에 start_depot/end_depot이 확정됨"""
        tasks = self._make_tasks()
        cfg = BaseColumnConfig()
        cfg.depot_policy = {"type": "single", "max_depot_changes": 0}
        cfg.max_tasks = 4
        cfg.max_gap = 60
        gen = BaseColumnGenerator(tasks, cfg)
        columns = gen.generate()

        # multi-trip column에는 depot이 확정되어야 함
        multi = [c for c in columns if len(c.trips) > 1]
        for col in multi:
            assert col.start_depot != "", f"col {col.id} has no start_depot"
            assert col.end_depot != "", f"col {col.id} has no end_depot"

    def test_wildcard_task_compatible_with_any_depot(self):
        """wildcard task(빈 allowed_depots)는 어떤 거점과도 호환"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="대저", end_location="대저",
                    allowed_depots=frozenset({"노포"})),
            # wildcard task: 같은 위치, 거점 미지정
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="대저", end_location="대저",
                    allowed_depots=frozenset()),
        ]
        cfg = BaseColumnConfig()
        cfg.depot_policy = {"type": "single"}
        cfg.max_tasks = 4
        cfg.max_gap = 60
        gen = BaseColumnGenerator(tasks, cfg)
        columns = gen.generate()

        # wildcard task가 노포 column에 포함될 수 있어야 함
        two_trip = [c for c in columns if len(c.trips) == 2]
        assert len(two_trip) > 0, "Wildcard task should be connectable"


# ════════════════════════════════════════════════════════════════
# 6. CrewDutyGenerator — depot-aware chain 결합
# ════════════════════════════════════════════════════════════════

class TestCrewDutyGeneratorDepot:
    """CrewDutyGenerator 거점 관련 로직"""

    def test_resolve_chain_depots_single(self):
        """같은 거점 trip만 있는 chain"""
        from domains.crew.duty_generator import CrewDutyGenerator
        chain = [
            TaskItem(id=1, dep_time=1020, arr_time=1060, duration=40,
                    start_location="대저", end_location="노포",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=1070, arr_time=1100, duration=30,
                    start_location="노포", end_location="대저",
                    allowed_depots=frozenset({"노포"})),
        ]
        # CrewDutyGenerator 인스턴스 없이 _resolve_chain_depots 테스트
        # (메서드가 self에 의존하지 않으므로 직접 호출 가능)
        from domains.crew.duty_generator import CrewDutyConfig
        cfg = CrewDutyConfig()
        gen = CrewDutyGenerator(chain, cfg)
        result = gen._resolve_chain_depots(chain)
        assert result == frozenset({"노포"})

    def test_resolve_chain_depots_mismatch(self):
        """서로 다른 거점 trip → 빈 교집합"""
        from domains.crew.duty_generator import CrewDutyGenerator, CrewDutyConfig
        chain = [
            TaskItem(id=1, dep_time=1020, arr_time=1060, duration=40,
                    start_location="대저", end_location="노포",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=1070, arr_time=1100, duration=30,
                    start_location="신평", end_location="신평",
                    allowed_depots=frozenset({"신평"})),
        ]
        cfg = CrewDutyConfig()
        gen = CrewDutyGenerator(chain, cfg)
        result = gen._resolve_chain_depots(chain)
        assert result == frozenset()

    def test_resolve_chain_depots_wildcard_ignored(self):
        """wildcard task는 교집합 계산에서 무시"""
        from domains.crew.duty_generator import CrewDutyGenerator, CrewDutyConfig
        chain = [
            TaskItem(id=1, dep_time=1020, arr_time=1060, duration=40,
                    start_location="대저", end_location="노포",
                    allowed_depots=frozenset({"노포"})),
            TaskItem(id=2, dep_time=1070, arr_time=1100, duration=30,
                    start_location="노포", end_location="대저",
                    allowed_depots=frozenset()),  # wildcard
        ]
        cfg = CrewDutyConfig()
        gen = CrewDutyGenerator(chain, cfg)
        result = gen._resolve_chain_depots(chain)
        assert result == frozenset({"노포"})


# ════════════════════════════════════════════════════════════════
# 7. 하위 호환성 (backward compatibility)
# ════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """기존 코드(depot 정책 미적용)와의 호환성"""

    def test_default_config_no_depot_effect(self):
        """기본 config(multi)에서는 depot이 column 생성에 영향 없음"""
        tasks = [
            TaskItem(id=1, dep_time=400, arr_time=440, duration=40,
                    start_location="A", end_location="A"),
            TaskItem(id=2, dep_time=450, arr_time=490, duration=40,
                    start_location="A", end_location="A"),
        ]
        cfg = BaseColumnConfig()
        assert not cfg.depot_policy_active
        gen = BaseColumnGenerator(tasks, cfg)
        columns = gen.generate()
        # depot 없이도 정상 동작해야 함
        assert len(columns) > 0

    def test_load_csv_without_depot_no_error(self):
        """depot 매핑 없이 CSV 로딩 — 기존 동작 그대로"""
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                "trip_id", "trip_dep_time", "trip_arr_time",
                "trip_duration", "dep_station", "arr_station", "direction",
            ])
            writer.writeheader()
            writer.writerow({
                "trip_id": "1", "trip_dep_time": "400", "trip_arr_time": "450",
                "trip_duration": "50", "dep_station": "A", "arr_station": "A",
                "direction": "up",
            })
        try:
            tasks = load_tasks_from_csv(path)
            assert len(tasks) == 1
            assert tasks[0].allowed_depots == frozenset()
        finally:
            os.unlink(path)
