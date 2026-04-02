"""
tests/test_depot_inference.py
거점 자동 추론 — detect_depot_names + infer_depot_mapping + confidence
"""

from __future__ import annotations

import pytest
from engine.column_generator import TaskItem
from engine.depot_inference import (
    detect_depot_names,
    infer_depot_mapping,
    format_depot_inference_for_ui,
    DepotInferenceResult,
)


# ════════════════════════════════════════════════════════════════
# 1. detect_depot_names
# ════════════════════════════════════════════════════════════════

class TestDetectDepotNames:

    def test_detect_from_dataframes(self):
        """근무인원 시트에서 거점 이름 감지"""
        import pandas as pd
        df = pd.DataFrame({
            "구분": ["전체", "주간사업"],
            "전체": [240, 72],
            "노포": [126, 38],
            "신평": [114, 34],
        })
        result = detect_depot_names([], dataframes={"1호선 근무인원": df})
        assert "노포" in result
        assert "신평" in result

    def test_no_crew_sheet(self):
        """관련 시트 없으면 빈 리스트"""
        import pandas as pd
        df = pd.DataFrame({"A": [1], "B": [2]})
        result = detect_depot_names([], dataframes={"random_sheet": df})
        assert result == []

    def test_empty_input(self):
        result = detect_depot_names([])
        assert result == []


# ════════════════════════════════════════════════════════════════
# 2. infer_depot_mapping
# ════════════════════════════════════════════════════════════════

class TestInferDepotMapping:

    def _make_trips(self, routes):
        """[(dep, arr), ...] → TaskItem 리스트"""
        return [
            TaskItem(id=i+1, dep_time=400+i*50, arr_time=440+i*50, duration=40,
                    start_location=dep, end_location=arr)
            for i, (dep, arr) in enumerate(routes)
        ]

    def test_basic_1line_pattern(self):
        """1호선 패턴: 신평 관련→신평, 나머지→전체"""
        trips = self._make_trips([
            ("노포", "다대포해수욕장"),    # 노포 포함 → {노포}... but also 다대포
            ("다대포해수욕장", "노포"),    # 노포 포함
            ("신평", "다대포해수욕장"),    # 신평 포함 → {신평}
            ("신평", "노포"),             # 신평+노포 → {노포, 신평}
        ])
        result = infer_depot_mapping(trips, ["노포", "신평"])

        # 신평→다대포: 신평만 포함 → {신평}
        assert result.mapping[3] == frozenset({"신평"})
        # 신평→노포: 양쪽 포함 → {노포, 신평}
        assert result.mapping[4] == frozenset({"노포", "신평"})
        # confidence > 0
        assert result.confidence > 0.5

    def test_no_depots(self):
        """거점 없으면 매핑 없음"""
        trips = self._make_trips([("A", "B")])
        result = infer_depot_mapping(trips, [])
        assert result.confidence == 0.0
        assert len(result.mapping) == 0

    def test_single_depot(self):
        """단일 거점이면 매핑 불필요"""
        trips = self._make_trips([("노포", "다대포")])
        result = infer_depot_mapping(trips, ["노포"])
        assert result.confidence == 0.0

    def test_all_trips_shared(self):
        """모든 trip이 모든 거점 포함 → 낮은 confidence"""
        trips = self._make_trips([
            ("노포", "신평"),
            ("신평", "노포"),
        ])
        result = infer_depot_mapping(trips, ["노포", "신평"])
        # 모든 trip이 양쪽 거점 → 전체 허용
        for tid, depots in result.mapping.items():
            assert depots == frozenset({"노포", "신평"})

    def test_unmatched_stations(self):
        """거점 이름과 역 이름이 안 맞으면 전체 허용"""
        trips = self._make_trips([("역A", "역B"), ("역C", "역D")])
        result = infer_depot_mapping(trips, ["노포", "신평"])
        # 모든 trip이 전체 허용
        for tid, depots in result.mapping.items():
            assert depots == frozenset({"노포", "신평"})

    def test_no_hardcoded_shared_concept(self):
        """'공용'이라는 별도 개념 없이 set으로만 표현"""
        trips = self._make_trips([("노포", "다대포해수욕장")])
        result = infer_depot_mapping(trips, ["노포", "신평"])
        # 결과는 frozenset — "공용" 문자열 없음
        assert isinstance(result.mapping[1], frozenset)

    def test_confidence_high_when_pattern_exists(self):
        """특정 거점 trip이 있으면 confidence 높음"""
        trips = self._make_trips([
            ("노포", "다대포"),
            ("다대포", "노포"),
            ("신평", "다대포"),  # 신평 특정
        ])
        result = infer_depot_mapping(trips, ["노포", "신평"])
        assert result.confidence >= 0.7

    def test_summary_format(self):
        """summary에 거점별 trip 수"""
        trips = self._make_trips([
            ("노포", "다대포"),
            ("신평", "다대포"),
        ])
        result = infer_depot_mapping(trips, ["노포", "신평"])
        assert isinstance(result.summary, dict)
        assert sum(result.summary.values()) == 2


# ════════════════════════════════════════════════════════════════
# 3. UI 포맷팅
# ════════════════════════════════════════════════════════════════

class TestFormatUI:

    def test_format_basic(self):
        result = DepotInferenceResult(
            depot_names=["노포", "신평"],
            summary={"신평": 23, "노포,신평": 345},
            confidence=0.85,
        )
        text = format_depot_inference_for_ui(result)
        assert "노포" in text
        assert "신평" in text
        assert "높음" in text

    def test_format_empty(self):
        result = DepotInferenceResult()
        text = format_depot_inference_for_ui(result)
        assert text == ""

    def test_format_low_confidence(self):
        result = DepotInferenceResult(
            depot_names=["A", "B"],
            summary={"A,B": 100},
            confidence=0.3,
            warnings=["매핑이 부정확할 수 있습니다."],
        )
        text = format_depot_inference_for_ui(result)
        assert "낮음" in text
        assert "⚠️" in text


# ════════════════════════════════════════════════════════════════
# 4. 실제 1호선 데이터 통합 테스트
# ════════════════════════════════════════════════════════════════

class TestRealData:

    def test_1line_with_real_trips(self):
        """1호선 trips.csv로 실제 추론"""
        import os
        trips_path = "uploads/190/normalized/trips.csv"
        if not os.path.exists(trips_path):
            pytest.skip("1호선 데이터 없음")

        from engine.column_generator import load_tasks_from_csv
        tasks = load_tasks_from_csv(trips_path)

        result = infer_depot_mapping(tasks, ["노포", "신평"])

        assert result.confidence > 0.5
        assert len(result.mapping) == len(tasks)
        # 신평 전용 trip이 존재해야 함
        sinpyeong_only = sum(
            1 for d in result.mapping.values() if d == frozenset({"신평"})
        )
        assert sinpyeong_only > 0, "신평 전용 trip이 없음"
