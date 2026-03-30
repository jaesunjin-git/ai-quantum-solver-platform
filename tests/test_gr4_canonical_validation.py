"""
GR-4 Canonical Model Validation 테스트
=======================================
L3 진입: catalog 등록 + type 일치 + range 통과 필수.
미검증 값이 L4(컴파일) 도달 시 즉시 차단.
"""

import pytest


# ============================================================
# validate_value() 테스트
# ============================================================

class TestValidateValue:
    """ParameterCatalog.validate_value() 검증"""

    def _get_catalog(self):
        from engine.policy.parameter_catalog import ParameterCatalog
        return ParameterCatalog("railway")

    def test_valid_numeric(self):
        """정상 숫자값은 에러 없음"""
        cat = self._get_catalog()
        assert cat.validate_value("max_driving_minutes", 360) is None

    def test_valid_string_numeric(self):
        """문자열 숫자도 float 변환 가능하면 통과"""
        cat = self._get_catalog()
        assert cat.validate_value("max_driving_minutes", "360") is None

    def test_out_of_range(self):
        """범위 벗어나면 에러 반환"""
        cat = self._get_catalog()
        err = cat.validate_value("max_driving_minutes", 999)
        assert err is not None
        assert "valid_range" in err

    def test_string_not_parseable_returns_error(self):
        """float 변환 불가 문자열은 에러 반환 (silent pass 아님!)"""
        cat = self._get_catalog()
        err = cat.validate_value("max_driving_minutes", "6시간")
        assert err is not None
        assert "숫자 변환 실패" in err

    def test_colon_format_returns_error(self):
        """HH:MM 형식 문자열도 에러 반환"""
        cat = self._get_catalog()
        err = cat.validate_value("max_driving_minutes", "225:33")
        assert err is not None
        assert "숫자 변환 실패" in err

    def test_unregistered_param_returns_error(self):
        """미등록 파라미터는 에러 반환"""
        cat = self._get_catalog()
        err = cat.validate_value("completely_unknown_param_xyz", 100)
        assert err is not None
        assert "catalog 미등록" in err

    def test_boolean_valid(self):
        """boolean 파라미터: True/False 통과"""
        cat = self._get_catalog()
        # is_overnight_crew가 boolean으로 등록되어 있다면
        entry = cat.resolve("is_overnight_crew")
        if entry and entry.type == "boolean":
            assert cat.validate_value("is_overnight_crew", True) is None
            assert cat.validate_value("is_overnight_crew", "true") is None

    def test_boolean_invalid(self):
        """boolean 파라미터: 숫자는 에러"""
        cat = self._get_catalog()
        entry = cat.resolve("is_overnight_crew")
        if entry and entry.type == "boolean":
            err = cat.validate_value("is_overnight_crew", 42)
            assert err is not None
            assert "boolean" in err

    def test_post_trip_training_registered(self):
        """post_trip_training_minutes가 catalog에 등록되어 있는지"""
        cat = self._get_catalog()
        entry = cat.resolve("post_trip_training_minutes")
        assert entry is not None
        assert entry.type == "scalar"
        assert entry.valid_range is not None


# ============================================================
# DataBinder string 파싱 테스트
# ============================================================

class TestDataBinderStringParsing:
    """DataBinder에서 string 파라미터의 자동 파싱 검증"""

    def test_parse_value_string_time_format(self):
        """225:33 → 225.55"""
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string("225:33") == pytest.approx(225.55)

    def test_parse_value_string_korean_hours(self):
        """6시간 → 360"""
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string("6시간") == pytest.approx(360.0)

    def test_parse_value_string_korean_minutes(self):
        """40분 → 40"""
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string("40분") == pytest.approx(40.0)

    def test_parse_value_string_pure_number(self):
        """360 → 360"""
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string("360") == pytest.approx(360.0)

    def test_parse_value_string_none(self):
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string(None) is None

    def test_parse_value_string_unparseable(self):
        from engine.gates.gate2_model_validate import _parse_value_string
        assert _parse_value_string("abc") is None
