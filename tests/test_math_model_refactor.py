"""
tests/test_math_model_refactor.py

P0 #1 — math_model.yaml 전면 리팩토링 검증
Phase B′: placeholder 치환 결과 의미적 동일성 검증
Phase D: 렌더링 함수 단위 테스트 + 확장 테스트
"""
from __future__ import annotations

import os
import sys
import pytest
import yaml

# 프로젝트 루트를 path에 추가
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.math_model_generator import (
    _load_modeling_rules,
    _render_variable_list,
    _render_variable_naming_rule,
    _render_set_naming_rule,
    _render_set_index_examples,
    _render_time_type_rule,
    _render_domain_rules,
    _render_objective_examples,
    _render_constraint_template_rules,
    _render_domain_checklist,
    _apply_modeling_rules,
)
from utils.prompt_loader import load_yaml_prompt


# ============================================================
# Phase A: modeling_rules.yaml 로드 검증
# ============================================================
class TestModelingRulesLoad:
    """modeling_rules.yaml이 올바르게 로드되는지 검증"""

    def test_railway_rules_load(self):
        rules = _load_modeling_rules("railway")
        assert rules, "railway modeling_rules.yaml 로드 실패"
        assert "variable_naming" in rules
        assert "set_naming" in rules
        assert "objective_examples" in rules
        assert "domain_specific_rules" in rules

    def test_railway_variable_naming_whitelist(self):
        rules = _load_modeling_rules("railway")
        vn = rules["variable_naming"]
        assert vn["mode"] == "whitelist"
        allowed_ids = [v["id"] for v in vn["allowed"]]
        assert "x" in allowed_ids
        assert "y" in allowed_ids
        assert "duty_start" in allowed_ids
        assert "duty_end" in allowed_ids
        assert "is_night" in allowed_ids
        assert "first_trip_dep" in allowed_ids
        assert "last_trip_arr" in allowed_ids
        # 레거시 변수는 allowed에 없어야 함
        assert "u" not in allowed_ids
        assert "s" not in allowed_ids
        assert "e" not in allowed_ids
        assert "mb" not in allowed_ids
        assert "s_next" not in allowed_ids

    def test_railway_set_naming_whitelist(self):
        rules = _load_modeling_rules("railway")
        sn = rules["set_naming"]
        assert sn["mode"] == "whitelist"
        allowed_ids = [s["id"] for s in sn["allowed"]]
        assert "I" in allowed_ids
        assert "J" in allowed_ids
        # 레거시 Set은 없어야 함
        assert "T" not in allowed_ids
        assert "D" not in allowed_ids

    def test_missing_domain_returns_empty(self):
        rules = _load_modeling_rules("nonexistent_domain_xyz")
        assert rules == {}

    def test_consistency_with_constraints_yaml(self):
        """modeling_rules.yaml의 변수/Set이 constraints.yaml과 일치하는지 검증"""
        rules = _load_modeling_rules("railway")
        constraints_path = os.path.join(ROOT, "knowledge", "domains", "railway", "constraints.yaml")
        with open(constraints_path, "r", encoding="utf-8") as f:
            constraints = yaml.safe_load(f)

        # constraints.yaml의 변수 ID와 modeling_rules의 allowed가 일치
        constraint_var_ids = set(constraints.get("variables", {}).keys())
        rule_var_ids = {v["id"] for v in rules["variable_naming"]["allowed"]}
        assert rule_var_ids == constraint_var_ids, (
            f"불일치: rules={rule_var_ids}, constraints={constraint_var_ids}"
        )

        # constraints.yaml의 Set ID와 일치 (overlap_pairs 제외 — 특수 Set)
        constraint_set_ids = {
            sid for sid in constraints.get("sets", {}).keys()
            if sid != "overlap_pairs"
        }
        rule_set_ids = {s["id"] for s in rules["set_naming"]["allowed"]}
        assert rule_set_ids == constraint_set_ids, (
            f"불일치: rules={rule_set_ids}, constraints={constraint_set_ids}"
        )


# ============================================================
# Phase B′: 렌더링 함수 단위 테스트
# ============================================================
class TestRenderFunctions:
    """각 렌더링 함수가 올바른 텍스트를 생성하는지 검증"""

    @pytest.fixture
    def rules(self):
        return _load_modeling_rules("railway")

    def test_render_variable_list(self, rules):
        text = _render_variable_list(rules)
        assert "의사결정 변수:" in text
        assert "x[i,j]" in text
        assert "y[j]" in text
        assert "duty_start[j]" in text
        # 레거시 변수명은 포함되지 않아야 함
        assert "u[d]" not in text
        assert "s[d]" not in text

    def test_render_variable_naming_rule(self, rules):
        text = _render_variable_naming_rule(rules)
        assert "★ 변수명 규칙:" in text
        assert "x[i,j]" in text
        assert "금지" in text
        assert "u[d]" in text  # explicit_warnings에 있으므로

    def test_render_set_naming_rule(self, rules):
        text = _render_set_naming_rule(rules)
        assert "★ Set 규칙:" in text
        assert "I(" in text
        assert "J(" in text
        assert "T" in text  # warnings에 있으므로

    def test_render_set_index_examples(self, rules):
        text = _render_set_index_examples(rules)
        assert "i in I" in text
        assert "j in J" in text

    def test_render_time_type_rule(self, rules):
        text = _render_time_type_rule(rules)
        assert "integer" in text
        assert "continuous" in text

    def test_render_domain_rules(self, rules):
        text = _render_domain_rules(rules)
        assert "[CRITICAL]" in text
        assert "R1:" in text
        assert "R2:" in text
        assert "[HIGH]" in text

    def test_render_objective_examples(self, rules):
        text = _render_objective_examples(rules)
        assert "min_duties" in text
        assert "sum(y[j] for j in J)" in text

    def test_render_constraint_template_rules(self, rules):
        text = _render_constraint_template_rules(rules)
        assert "인덱스 규칙:" in text
        assert "변수명 규칙:" in text
        assert "I" in text
        assert "J" in text

    def test_render_domain_checklist(self, rules):
        text = _render_domain_checklist(rules)
        assert "I" in text
        assert "J" in text
        assert "(7)" in text or "변수명" in text

    def test_empty_rules_graceful(self):
        """규칙이 없어도 에러 없이 빈 문자열 반환"""
        empty = {}
        assert _render_variable_list(empty) == ""
        assert _render_variable_naming_rule(empty) == ""
        assert _render_set_naming_rule(empty) == ""
        assert _render_domain_rules(empty) == ""
        assert _render_objective_examples(empty) == ""


# ============================================================
# Phase B′: placeholder 치환 검증
# ============================================================
class TestApplyModelingRules:
    """_apply_modeling_rules가 math_model.yaml의 placeholder를 올바르게 치환하는지 검증"""

    def test_no_unresolved_placeholders(self):
        """치환 후 도메인 placeholder가 남아있지 않아야 함"""
        raw_config = load_yaml_prompt("railway", "math_model")
        rules = _load_modeling_rules("railway")
        config = _apply_modeling_rules(raw_config, rules)

        domain_placeholders = [
            "{variable_list_text}", "{set_index_examples}", "{for_each_examples}",
            "{set_naming_rule}", "{variable_naming_rule}", "{time_type_rule}",
            "{domain_rules_text}", "{objective_examples_text}",
            "{constraint_template_rules}", "{domain_checklist}",
        ]

        # rules 리스트 검증
        all_rules_text = "\n".join(config.get("rules", []))
        for ph in domain_placeholders:
            assert ph not in all_rules_text, f"미치환 placeholder 발견: {ph} in rules"

        # objective_rules 검증
        obj_rules_text = "\n".join(config.get("objective_rules", []))
        for ph in domain_placeholders:
            assert ph not in obj_rules_text, f"미치환 placeholder 발견: {ph} in objective_rules"

        # checklist 검증
        checklist = config.get("checklist", "")
        for ph in domain_placeholders:
            assert ph not in checklist, f"미치환 placeholder 발견: {ph} in checklist"

        # constraint_templates 섹션 검증
        ct = config.get("sections", {}).get("constraint_templates", "")
        for ph in domain_placeholders:
            assert ph not in ct, f"미치환 placeholder 발견: {ph} in constraint_templates"

    def test_railway_rules_contain_ij_convention(self):
        """치환 후 I/J 컨벤션이 적용되어야 함"""
        raw_config = load_yaml_prompt("railway", "math_model")
        rules = _load_modeling_rules("railway")
        config = _apply_modeling_rules(raw_config, rules)

        all_text = "\n".join(config.get("rules", []))
        # I/J 컨벤션
        assert "x[i,j]" in all_text
        assert "y[j]" in all_text
        assert "duty_start[j]" in all_text
        # 레거시 T/D 컨벤션 규칙이 없어야 함 (warnings 문구 제외)
        assert "T(trips)와 D(duties)만 사용" not in all_text
        assert "x[i,d]" not in all_text

    def test_empty_rules_no_crash(self):
        """modeling_rules가 없는 도메인도 에러 없이 동작"""
        raw_config = load_yaml_prompt("railway", "math_model")
        config = _apply_modeling_rules(raw_config, {})
        # 빈 규칙으로 치환 — placeholder가 빈 문자열로 대체됨
        assert config is not None
        assert len(config.get("rules", [])) > 0

    def test_runtime_placeholders_preserved(self):
        """runtime에서 치환되는 placeholder({templates_text} 등)는 유지되어야 함"""
        raw_config = load_yaml_prompt("railway", "math_model")
        rules = _load_modeling_rules("railway")
        config = _apply_modeling_rules(raw_config, rules)

        ct = config.get("sections", {}).get("constraint_templates", "")
        assert "{templates_text}" in ct, "runtime placeholder {templates_text}가 제거됨"

        template = config.get("template", "")
        assert "{rules_text}" in template, "runtime placeholder {rules_text}가 제거됨"
        assert "{csv_summary}" in template, "runtime placeholder {csv_summary}가 제거됨"

    def test_original_config_not_mutated(self):
        """_apply_modeling_rules가 원본 config를 변경하지 않음"""
        raw_config = load_yaml_prompt("railway", "math_model")
        original_rules = list(raw_config.get("rules", []))
        rules = _load_modeling_rules("railway")
        _apply_modeling_rules(raw_config, rules)
        # 원본이 변경되지 않았는지 확인
        assert raw_config.get("rules", []) == original_rules


# ============================================================
# Phase D: math_model.yaml 구조 검증
# ============================================================
class TestMathModelYamlStructure:
    """리팩토링된 math_model.yaml의 구조 검증"""

    def test_version_upgraded(self):
        config = load_yaml_prompt("railway", "math_model")
        assert config.get("version") == "3.0"

    def test_no_crew_hardcoding_in_rules(self):
        """공용 규칙에 crew/railway 하드코딩이 없어야 함"""
        config = load_yaml_prompt("railway", "math_model")
        rules = config.get("rules", [])
        hardcoded_patterns = [
            "x[i,d]", "u[d]", "s[d]", "e[d]", "mb[d]", "s_next[d]",
            "T(trips)", "D(duties)", "duty d에 배정", "duty d 활성화",
        ]
        for rule in rules:
            for pattern in hardcoded_patterns:
                assert pattern not in rule, (
                    f"하드코딩 발견: '{pattern}' in rule: {rule[:80]}..."
                )

    def test_no_crew_hardcoding_in_checklist(self):
        """checklist에 crew/railway 하드코딩이 없어야 함"""
        config = load_yaml_prompt("railway", "math_model")
        checklist = config.get("checklist", "")
        assert "T와 D만" not in checklist
        assert "x[i,d]" not in checklist
        assert "u[d]" not in checklist

    def test_domain_placeholders_present(self):
        """도메인 placeholder가 존재해야 함"""
        config = load_yaml_prompt("railway", "math_model")
        rules_text = "\n".join(config.get("rules", []))
        assert "{variable_list_text}" in rules_text
        assert "{set_naming_rule}" in rules_text
        assert "{variable_naming_rule}" in rules_text

    def test_common_rules_preserved(self):
        """공용 규칙(JSON 스키마, lhs/rhs 등)은 유지되어야 함"""
        config = load_yaml_prompt("railway", "math_model")
        rules_text = "\n".join(config.get("rules", []))
        assert "JSON만 출력" in rules_text
        assert "lhs/operator/rhs" in rules_text
        assert "1계층 파라미터" in rules_text
        assert "snake_case" in rules_text

    def test_soft_constraint_rules_unchanged(self):
        """soft_constraint_rules는 공용이므로 변경 없어야 함"""
        config = load_yaml_prompt("railway", "math_model")
        soft_rules = config.get("soft_constraint_rules", [])
        assert len(soft_rules) == 5
        assert any("slack" in r for r in soft_rules)

    def test_sections_structure_preserved(self):
        """sections의 runtime placeholder 구조 유지"""
        config = load_yaml_prompt("railway", "math_model")
        sections = config.get("sections", {})
        assert "{stage}" in sections.get("confirmed_problem", "")
        assert "{hard_count}" in sections.get("hard_constraints", "")
        assert "{soft_count}" in sections.get("soft_constraints", "")
        assert "{templates_text}" in sections.get("constraint_templates", "")
        assert "{param_list_text}" in sections.get("parameters", "")
