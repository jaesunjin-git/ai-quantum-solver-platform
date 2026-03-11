from __future__ import annotations
"""
domains/crew/skills/problem_definition.py

Problem Definition Skill – TASK 3 Refactored Version.

핵심 변경:
  1. 범용 도메인 로더 사용 (knowledge/domain_loader.py)
  2. 제약조건 타입별 분기 처리 (single_param, compound, conditional, pairwise, data_derived)
  3. Phase 1 결과(phase1/) 기반 적용 가능성 필터링
  4. One-by-One LLM 추출 (NOT_FOUND 허용)
  5. 토폴로지 인식 필터링
  6. 하드코딩 없는 범용 구조
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
import pandas as pd

from domains.crew.session import CrewSession, save_session_state

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[3]
_UPLOAD_BASE = _BASE / "uploads"


def _load_yaml(rel_path: str) -> dict:
    full = _BASE / rel_path
    if not full.exists():
        logger.warning(f"YAML not found: {full}")
        return {}
    with open(full, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_safe_dir(project_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(project_id))
    return _UPLOAD_BASE / safe_id


class ProblemDefinitionSkill:

    def __init__(self):
        self.taxonomy = _load_yaml("knowledge/taxonomy.yaml")
        self.data_detection = _load_yaml("knowledge/data_detection.yaml")
        self.matching_rules = _load_yaml("knowledge/matching_rules.yaml")
        self.prompt_config = _load_yaml("prompts/problem_definition.yaml")
        self.extraction_prompts = _load_yaml("prompts/constraint_extraction.yaml")

        # data_detection.yaml에서 데이터 유형별 키워드 맵
        self._detection_keywords: Dict[str, List[List[str]]] = {}
        self._extraction_keys: Dict[str, List[str]] = {}
        for dtype, dinfo in self.data_detection.get("data_types", {}).items():
            self._detection_keywords[dtype] = dinfo.get("column_keywords", [])
            self._extraction_keys[dtype] = dinfo.get("extraction_keys", [])

        # matching_rules
        self._matching_rules = self.matching_rules.get("rules", {})

        # taxonomy stage -> variant_group
        self._stage_variant_group: Dict[str, str] = {}
        for stage_key, stage_info in self.taxonomy.get("stages", {}).items():
            self._stage_variant_group[stage_key] = stage_info.get("variant_group", stage_key)

        logger.info("ProblemDefinitionSkill init (TASK 3 refactored)")

    # ──────────────────────────────────────
    # 도메인 지식 로드 (범용 로더 사용)
    # ──────────────────────────────────────
    def _load_domain(self, state):
        """범용 도메인 로더를 통해 DomainKnowledge 반환"""
        try:
            from knowledge.domain_loader import (
                load_domain_knowledge, detect_domain_from_keywords,
                resolve_domain_alias,
            )
        except ImportError:
            logger.warning("domain_loader not available, falling back")
            return None

        domain = state.detected_domain or "generic"

        # 별칭 해석 후 시도 (crew → railway 등, domain_aliases.yaml 기반)
        canonical = resolve_domain_alias(domain)
        dk = load_domain_knowledge(canonical)
        if dk.hard_constraints or dk.raw_single:
            return dk

        # 키워드 기반 도메인 탐지
        search_text = " ".join(state.uploaded_files or [])
        if state.csv_summary:
            search_text += " " + state.csv_summary
        detected = detect_domain_from_keywords(search_text)
        if detected:
            dk = load_domain_knowledge(detected)
            if dk.hard_constraints or dk.raw_single:
                return dk

        return dk

    def _get_available_constraints(
        self, dk, current_hard: dict, current_soft: dict
    ) -> list:
        """현재 모델에 없는 constraints.yaml 카탈로그 제약 목록 반환."""
        if not dk:
            return []
        used = set(current_hard.keys()) | set(current_soft.keys())
        available = []
        all_names = dk.all_constraint_names()
        for name in all_names:
            if name in used:
                continue
            cdata = dk.get_constraint(name)
            if not cdata or not isinstance(cdata, dict):
                continue
            available.append({
                "name": name,
                "description": cdata.get("description", ""),
                "default_category": cdata.get("default_category",
                                               cdata.get("_meta", {}).get("original_category", "hard")),
                "fixed_category": cdata.get("fixed_category",
                                             cdata.get("_meta", {}).get("fixed_category", False)),
                "parameters": cdata.get("parameters", []),
                "expression_template": cdata.get("expression_template", ""),
                "typical_range": cdata.get("typical_range"),
                "penalty_weight": cdata.get("penalty_weight"),
            })
        return available

    # ──────────────────────────────────────
    # public entry point
    # ──────────────────────────────────────
    async def handle(
        self, model, session: CrewSession, project_id: str,
        message: str, params: Dict
    ) -> Dict:
        state = session.state

        # 구조화된 이벤트 처리 (최우선)
        if params.get("event_type") == "problem_definition_confirm":
            return await self._handle_pd_confirm(session, project_id, params.get("event_data", {}))

        # 이미 제안을 보냈고 사용자 응답 대기 중
        if state.problem_definition_proposed and not state.problem_defined:
            return await self._handle_user_response(model, session, project_id, message)

        # 첫 진입: 분석 결과 + Phase 1 기반 제안 생성
        dk = self._load_domain(state)
        detected_data_types = self._detect_data_types(state)
        problem_type = self._determine_problem_type(state, dk, detected_data_types)
        topology = self._detect_topology(state, project_id)
        objective = self._determine_objective(problem_type, dk)

        # ★ 핵심: 3단계 제약조건 결정
        constraints = await self._determine_constraints_phased(
            model, state, project_id, dk, detected_data_types, topology
        )

        # 파라미터 수집 (Phase 1 데이터 우선)
        parameters = self._collect_parameters(state, project_id, dk, constraints)

        proposal = {
            "stage": problem_type.get("stage", "task_generation"),
            "variant": problem_type.get("variant"),
            "topology": topology,
            "detected_data_types": list(detected_data_types),
            "objective": objective,
            "hard_constraints": constraints.get("hard", {}),
            "soft_constraints": constraints.get("soft", {}),
            "parameters": parameters,
        }

        # 미사용 제약조건 카탈로그 (템플릿 기반 추가용)
        proposal["available_constraints"] = self._get_available_constraints(
            dk, constraints.get("hard", {}), constraints.get("soft", {})
        )

        state.problem_definition = proposal
        state.problem_definition_proposed = True
        save_session_state(project_id, state)

        response_text = self._format_proposal(state, dk, proposal)

        return {
            "type": "problem_definition",
            "text": response_text,
            "data": {
                "view_mode": "problem_definition",
                "proposal": proposal,
                "agent_status": "problem_definition_proposed",
            },
            "options": [
                {"label": "confirm", "action": "send", "message": "confirm"},
                {"label": "modify", "action": "send", "message": "modify"},
                {"label": "reanalyze", "action": "send", "message": "reanalyze"},
            ],
        }

    # ──────────────────────────────────────
    # 데이터 유형 감지
    # ──────────────────────────────────────
    def _detect_data_types(self, state) -> Set[str]:
        detected = set()
        facts = state.data_facts or {}
        all_columns = facts.get("all_columns", {})

        for sheet_key, columns in all_columns.items():
            col_text = " ".join(str(c).lower() for c in columns)
            for dtype, keyword_groups in self._detection_keywords.items():
                if dtype in detected:
                    continue
                for group in keyword_groups:
                    if any(kw.lower() in col_text for kw in group):
                        detected.add(dtype)
                        break

        if state.data_profile and isinstance(state.data_profile, dict):
            for sheet_key, info in state.data_profile.get("files", {}).items():
                if info.get("structure") == "non_tabular_block":
                    detected.add("existing_duty")

        logger.info(f"Detected data types: {detected}")
        return detected

    # ──────────────────────────────────────
    # 토폴로지 감지 (Phase 1 데이터 기반)
    # ──────────────────────────────────────
    def _detect_topology(self, state, project_id: str) -> Optional[str]:
        """Phase 1의 timetable_rows.csv에서 토폴로지를 판별"""
        phase1_dir = _get_safe_dir(project_id) / "phase1"
        trips_file = phase1_dir / "timetable_rows.csv"
        if not trips_file.exists():
            return None

        try:
            df = pd.read_csv(str(trips_file))
            if df.empty:
                return None

            # 종착역 수
            terminals = set()
            if "dep_station" in df.columns:
                terminals.update(df["dep_station"].dropna().unique())
            if "arr_station" in df.columns:
                terminals.update(df["arr_station"].dropna().unique())
            terminal_count = len(terminals)

            # 방향 값 수
            direction_values = 0
            if "direction" in df.columns:
                direction_values = df["direction"].nunique()

            # 노선 ID 수
            line_count = 1
            for col in df.columns:
                if any(kw in str(col).lower() for kw in ["line", "노선", "line_id"]):
                    line_count = df[col].nunique()
                    break

            # 순환 감지
            if "dep_station" in df.columns and "arr_station" in df.columns:
                same_start_end = (df["dep_station"] == df["arr_station"]).sum()
                circular_ratio = same_start_end / len(df) if len(df) > 0 else 0
                if circular_ratio > 0.3:
                    return "circular"

            # 다중노선
            if line_count >= 2 or terminal_count >= 4:
                return "multi_line"

            # 단방향
            if direction_values <= 1:
                return "single_line_unidirectional"

            # 양방향 (기본)
            if terminal_count <= 3 and direction_values == 2:
                return "single_line_bidirectional"

            return "single_line_bidirectional"

        except Exception as e:
            logger.warning(f"Topology detection failed: {e}")
            return None

    # ──────────────────────────────────────
    # 문제 유형 결정
    # ──────────────────────────────────────
    def _determine_problem_type(self, state, dk, detected_data_types: Set[str]) -> dict:
        best = {"stage": "task_generation", "variant": None, "confidence": 0.0}

        for rule_name, rule in self._matching_rules.items():
            required = set(rule.get("required_data", []))
            base_conf = rule.get("base_confidence", 0.5)
            stage = rule.get("recommended_stage", "task_generation")

            if not required.issubset(detected_data_types):
                continue

            confidence = base_conf
            for boost in rule.get("boost_conditions", []):
                condition = boost.get("condition", "")
                boost_val = boost.get("boost", 0)
                if "detected" in condition:
                    for dtype in detected_data_types:
                        if dtype in condition:
                            confidence += boost_val
                            break
                elif "all five data types" in condition:
                    if len(detected_data_types) >= 5:
                        confidence += boost_val

            confidence = min(confidence, 1.0)
            if confidence > best["confidence"]:
                best = {"stage": stage, "variant": None, "confidence": confidence, "matched_rule": rule_name}

        # variant 결정
        if dk:
            variants_section = dk.index.get("network_topologies", {}) if dk.index else {}
            # 도메인 YAML에 problem_variants가 있으면 사용 (단일 파일 호환)
            if dk.raw_single:
                variants = dk.raw_single.get("problem_variants", {})
                stage = best.get("stage", "task_generation")
                variant_group_key = self._stage_variant_group.get(stage, stage)
                variant_group = variants.get(variant_group_key, {})
                if variant_group:
                    best["variant"] = next(iter(variant_group.keys()), None)

        logger.info(f"Problem type: {best}")
        return best

    # ──────────────────────────────────────
    # 목적함수 결정
    # ──────────────────────────────────────
    def _determine_objective(self, problem_type: dict, dk) -> dict:
        stage = problem_type.get("stage", "task_generation")
        stage_info = self.taxonomy.get("stages", {}).get(stage, {})
        typical_objectives = stage_info.get("typical_objectives", [])

        # 템플릿에서 목적함수 후보 가져오기
        obj_templates = {}
        if dk and dk.templates:
            obj_templates = dk.templates.get("objective_templates", {})

        primary = typical_objectives[0] if typical_objectives else "minimize"
        description = primary.replace("_", " ")

        # 템플릿에서 상세 설명 조회
        for tname, tdata in obj_templates.items():
            if tname == primary or primary in tname:
                description = tdata.get("description", description)
                break

        alternatives = []
        for obj in typical_objectives[1:3]:
            alt_desc = obj.replace("_", " ")
            for tname, tdata in obj_templates.items():
                if tname == obj or obj in tname:
                    alt_desc = tdata.get("description", alt_desc)
                    break
            alternatives.append({"target": obj, "description": alt_desc})

        return {
            "type": "minimize",
            "target": primary,
            "description": description,
            "alternatives": alternatives,
        }

    # ══════════════════════════════════════
    # ★ 3단계 제약조건 결정 (핵심 리팩토링)
    # ══════════════════════════════════════
    async def _determine_constraints_phased(
        self, model, state, project_id: str, dk,
        detected_data_types: Set[str], topology: Optional[str]
    ) -> dict:
        """
        Phase A: 적용 가능성 필터링
        Phase B: 타입별 값 추출
        Phase C: 결과 정리 (사용자 확인용)
        """
        if not dk:
            return {"hard": {}, "soft": {}}

        hard_results = {}
        soft_results = {}

        # Phase 1 데이터 로드
        phase1_data = self._load_phase1_data(project_id)

        # ── Phase A: 적용 가능성 필터링 ──
        for cname, cdata in dk.hard_constraints.items():
            applicability = self._check_applicability(
                cdata, detected_data_types, topology, phase1_data
            )
            if not applicability["applicable"]:
                logger.debug(f"Constraint {cname} skipped: {applicability['reason']}")
                continue

            # ── Phase B: 타입별 값 추출 ──
            ctype = cdata.get("type", "single_param")
            extraction = await self._extract_constraint_value(
                model, cname, cdata, ctype, phase1_data, state
            )

            # _meta에서 changeable/fixed 정보 추출
            c_meta = dk.get_constraint_meta(cname) if dk else {}
            c_fixed = c_meta.get('fixed_category', False)
            c_changeable = not c_fixed
            
            hard_results[cname] = {
                "name_ko": cdata.get("name_ko", cname),
                "type": ctype,
                "description": cdata.get("description", ""),
                "status": extraction.get("status", "unknown"),
                "values": extraction.get("values", {}),
                "computation_phase": extraction.get("computation_phase"),
                "fixed": c_fixed,
                "changeable": c_changeable,
            }

        # Soft constraints
        for cname, cdata in dk.soft_constraints.items():
            applicability = self._check_applicability(
                cdata, detected_data_types, topology, phase1_data
            )
            if not applicability["applicable"]:
                continue

            # ★ CHANGED: YAML의 weight 필드를 우선 사용
            yaml_weight = cdata.get("weight")
            if yaml_weight is not None:
                default_weight = float(yaml_weight)
                weight_range = cdata.get("weight_range", [max(0.1, default_weight - 0.5), default_weight + 0.5])
            else:
                weight_range = cdata.get("weight_range", [0.1, 0.5])
                default_weight = round((weight_range[0] + weight_range[1]) / 2, 2)

            # _meta에서 changeable/fixed 정보 추출
            # ── Phase B: soft도 값 추출 ──
            ctype_s = cdata.get('type', 'single_param')
            extraction_s = await self._extract_constraint_value(
                model, cname, cdata, ctype_s, phase1_data, state
            )
            s_values = extraction_s.get('values', {})
            s_status = extraction_s.get('status', 'default')

            # _meta에서 changeable/fixed 정보 추출
            s_meta = dk.get_constraint_meta(cname) if dk else {}
            s_fixed = s_meta.get('fixed_category', False)
            s_changeable = not s_fixed

            soft_results[cname] = {
                'name_ko': cdata.get('name_ko', cname),
                'type': ctype_s,
                'description': cdata.get('description', ''),
                'weight': default_weight,
                'weight_range': weight_range,
                'status': s_status,
                'values': s_values,
                'fixed': s_fixed,
                'changeable': s_changeable,
            }

        return {"hard": hard_results, "soft": soft_results}

    # ── Phase A: 적용 가능성 검사 ──
    def _check_applicability(
        self, cdata: dict, detected_data_types: Set[str],
        topology: Optional[str], phase1_data: dict
    ) -> dict:
        """제약조건이 현재 데이터/토폴로지에 적용 가능한지 판단"""

        # 1. required_data_types 확인
        required = cdata.get("required_data_types", [])
        if required:
            # timetable은 Phase 1에서 timetable_rows.csv로 변환되므로 항상 있다고 봄
            effective_detected = set(detected_data_types)
            if phase1_data.get("has_trips"):
                effective_detected.add("timetable")
            # Phase 1에서 파라미터가 추출되었으면 work_regulations 존재
            if phase1_data.get("params_raw") and len(phase1_data["params_raw"]) > 0:
                effective_detected.add("work_regulations")
                # 텍스트 파일에서 추출된 파라미터가 있으면 crew_info도 추가
                text_params = [p for p in phase1_data["params_raw"] if "text:" in str(p.get("source",""))]
                if text_params:
                    effective_detected.add("crew_info")

            missing = [r for r in required if r not in effective_detected]
            if missing:
                return {"applicable": False, "reason": f"missing data types: {missing}"}

        # 2. applicability 조건 확인
        applicability = cdata.get("applicability", "all")
        if isinstance(applicability, dict):
            conditions = applicability.get("conditions", [])
            for cond in conditions:
                cond_lower = cond.lower()
                if "crew_info" in cond_lower and "crew_info" not in detected_data_types:
                    return {"applicable": False, "reason": f"condition not met: {cond}"}
                if "preference_data" in cond_lower and "preference_data" not in detected_data_types:
                    return {"applicable": False, "reason": f"condition not met: {cond}"}
                if "roster_assignment" in cond_lower:
                    pass  # 추후 단계 구분 시 필터링

        return {"applicable": True, "reason": "ok"}

    # ── Phase B: 타입별 값 추출 ──
    async def _extract_constraint_value(
        self, model, cname: str, cdata: dict, ctype: str,
        phase1_data: dict, state
    ) -> dict:
        """제약조건 타입에 따라 값을 추출한다."""

        # ★ CHANGED: YAML의 의미적 type → 추출 방식 매핑
        has_single_param = cdata.get("parameter") is not None
        has_compound_params = cdata.get("parameters") is not None

        # 직접 매핑되는 기존 타입
        if ctype == "single_param":
            return self._extract_single_param(cname, cdata, phase1_data)

        elif ctype == "compound":
            return self._extract_compound(cname, cdata, phase1_data)

        elif ctype == "conditional":
            return self._extract_conditional(cname, cdata, phase1_data)

        elif ctype == "pairwise":
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        elif ctype == "data_derived":
            return self._extract_data_derived(cname, cdata, phase1_data)

        # ★ NEW: YAML 의미적 타입 → 추출 방식 자동 결정

        # classification: 주간/야간 구분 등 자동 세팅 (compound보다 먼저 체크)
        elif ctype in ("classification",):
            auto_values = {}
            params_raw = cdata.get("parameters")
            if isinstance(params_raw, list):
                for p in params_raw:
                    if isinstance(p, str):
                        ref_val = self._lookup_reference_value(p)
                        if ref_val is not None:
                            auto_values[p] = {"value": ref_val, "source": "reference_default", "confidence": 0.6}
                        else:
                            auto_values[p] = {"value": None, "source": "auto_model_variable"}
            if auto_values:
                return {
                    "status": "confirmed",
                    "values": auto_values,
                    "computation_phase": "compile_time",
                }
            return {
                "status": "computed_in_phase2",
                "values": {},
                "computation_phase": "semantic_normalization",
            }

        # parameter 필드가 있으면 single_param으로 처리
        elif has_single_param:
            return self._extract_single_param(cname, cdata, phase1_data)

        # parameters (dict 또는 list)가 있으면 compound로 처리
        elif has_compound_params:
            params_raw = cdata.get("parameters")
            if isinstance(params_raw, list):
                converted = {}
                for p in params_raw:
                    if isinstance(p, str):
                        converted[p] = {"typical_range": cdata.get("typical_range", [])}
                cdata_copy = dict(cdata)
                cdata_copy["parameters"] = converted
                return self._extract_compound(cname, cdata_copy, phase1_data)
            return self._extract_compound(cname, cdata, phase1_data)

        # parameter 없는 구조적 제약 (equality, logical 등)
        elif ctype in ("equality", "logical"):
            return {
                "status": "confirmed",
                "values": {},
                "computation_phase": "compile_time",
            }

        # constant 타입 (big_m 등): 자동 세팅
        elif ctype in ("constant",):
            param_name = cdata.get("parameter")
            if param_name:
                ref_val = self._lookup_reference_value(param_name)
                if param_name == "big_m" and ref_val:
                    # big_m은 데이터 기반 자동 조정: max(기본값, 최대도착시각*2)
                    max_arr = 1440
                    try:
                        import pandas as _pd
                        from pathlib import Path as _Path
                        _base = _Path(__file__).resolve().parents[3] / "uploads"
                        for _pid in sorted(_base.iterdir(), reverse=True):
                            _tt = _pid / "phase1" / "timetable_rows.csv"
                            if _tt.is_file():
                                _df = _pd.read_csv(str(_tt))
                                if "trip_arr_time" in _df.columns:
                                    max_arr = max(max_arr, int(_df["trip_arr_time"].max()))
                                break
                    except Exception:
                        pass
                    auto_val = max(int(ref_val), max_arr + 60)  # 최대도착+60분 여유, 최소 1440
                    return {
                        "status": "confirmed",
                        "values": {param_name: {"value": auto_val, "source": "auto_computed", "confidence": 1.0}},
                    }
                elif ref_val is not None:
                    return {
                        "status": "confirmed",
                        "values": {param_name: {"value": ref_val, "source": "reference_default", "confidence": 0.9}},
                    }

        return {"status": "unknown_type", "values": {}}

    def _extract_single_param(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        param_name = cdata.get("parameter")
        if not param_name:
            return {"status": "confirmed", "values": {}}

        # Phase 1 파라미터에서 검색
        value = self._search_phase1_params(param_name, cdata, phase1_data)
        if value is not None:
            return {
                "status": "extracted",
                "values": {param_name: {"value": value, "source": "phase1_data", "confidence": 0.8}},
            }

        # ★ NEW: reference_ranges에서 참고 범위 및 기본값 조회
        ref_range = cdata.get("typical_range")
        ref_value = self._lookup_reference_value(param_name)

        # reference_default가 있고 typical_range 내이면 자동 적용
        if ref_value is not None:
            return {
                "status": "confirmed",
                "values": {param_name: {
                    "value": ref_value,
                    "source": "reference_default",
                    "confidence": 0.85,
                    "reference_range": ref_range,
                    "note": "reference_ranges.yaml 기본값 자동 적용",
                }},
            }

        return {
            "status": "user_input_required",
            "values": {param_name: {
                "value": None,
                "source": "user_input_required",
                "reference_range": ref_range,
                "reference_default": ref_value,
            }},
        }


    # NEW: reference_ranges.yaml lookup
    def _lookup_reference_value(self, param_name: str):
        """reference_ranges.yaml에서 첫 번째 매칭되는 기본값 반환"""
        if not hasattr(self, "_reference_cache"):
            self._reference_cache = self._load_reference_ranges()
        return self._reference_cache.get(param_name)

    def _load_reference_ranges(self) -> dict:
        """모든 reference_ranges.yaml에서 파라미터 기본값 수집"""
        import os, yaml
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        domains_dir = os.path.join(base, "knowledge", "domains")
        values = {}
        if not os.path.isdir(domains_dir):
            return values
        for dname in os.listdir(domains_dir):
            rpath = os.path.join(domains_dir, dname, "reference_ranges.yaml")
            if not os.path.isfile(rpath):
                continue
            try:
                with open(rpath, "r", encoding="utf-8") as f:
                    rdata = yaml.safe_load(f) or {}
            except Exception:
                continue
            for region_key, region in rdata.items():
                if isinstance(region, dict) and "values" in region:
                    for k, v in region["values"].items():
                        if k not in values:
                            values[k] = v
        return values

    def _find_best_cdata_for_param(self, param_name: str, fallback_cdata: dict) -> dict:
        """파라미터가 독립 제약에서 정의되어 있으면 그 제약의 cdata 반환.
        예: prep_time_minutes는 max_work_time의 sub-param이지만,
            prep_cleanup_time 제약에서 독립 정의 → 그 hints가 더 정확."""
        if not hasattr(self, '_dk_ref'):
            return fallback_cdata
        dk = self._dk_ref
        if not dk:
            return fallback_cdata

        # hard constraints에서 이 param을 직접 정의하는 제약 찾기
        for cname, cdef in dk.hard_constraints.items():
            if not isinstance(cdef, dict):
                continue
            # single_param으로 직접 정의
            if cdef.get("parameter") == param_name:
                return cdef
            # compound의 sub-param으로 정의 (다른 제약에서)
            sub_params = cdef.get("parameters") or {}
            if param_name in sub_params and cdef is not fallback_cdata:
                # 이 제약의 hints가 더 구체적인지 확인
                # ★ CHANGED: detection_hints가 list일 수도 dict일 수도 있음
                raw_this = cdef.get("detection_hints") or []
                this_hints = raw_this if isinstance(raw_this, list) else raw_this.get("ko", [])
                raw_fall = fallback_cdata.get("detection_hints") or []
                fall_hints = raw_fall if isinstance(raw_fall, list) else raw_fall.get("ko", [])
                if this_hints != fall_hints:
                    return cdef

        return fallback_cdata
    def _extract_compound(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        params = cdata.get('parameters', {})
        # parameters가 list인 경우 dict로 변환
        if isinstance(params, list):
            params = {p: {} for p in params}
        values = {}
        all_found = True
    
        for pname in params:
            pinfo = params[pname] if isinstance(params[pname], dict) else {}
            better_cdata = self._find_best_cdata_for_param(pname, cdata)
            value = self._search_phase1_params(pname, better_cdata, phase1_data)
            if value is not None:
                values[pname] = {'value': value, 'source': 'phase1_data', 'confidence': 0.8}
            else:
                # Phase 1에서 못 찾으면 reference_ranges에서 기본값 조회
                ref_value = self._lookup_reference_value(pname)
                ref_range = pinfo.get('typical_range') if isinstance(pinfo, dict) else None
                if ref_value is not None:
                    values[pname] = {
                        'value': ref_value,
                        'source': 'reference_default',
                        'confidence': 0.85,
                        'reference_range': ref_range,
                        'note': 'reference_ranges.yaml 기본값',
                    }
                else:
                    values[pname] = {
                        'value': None,
                        'source': 'user_input_required',
                        'reference_range': ref_range,
                    }
                    all_found = False
    
        status = 'extracted' if all_found else (
            'partial' if any(v.get('value') is not None for v in values.values())
            else 'user_input_required'
        )
        return {'status': status, 'values': values}

    def _extract_conditional(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        trigger = cdata.get("trigger", {})
        consequence = cdata.get("consequence", {})
        values = {}

        # trigger threshold
        threshold_param = trigger.get("threshold_param")
        if threshold_param:
            val = self._search_phase1_params(threshold_param, cdata, phase1_data)
            values[threshold_param] = {
                "value": val,
                "source": "phase1_data" if val is not None else "user_input_required",
            }

        # consequence value
        consequence_param = consequence.get("parameter")
        if consequence_param:
            # Merge consequence hints (context_must, typical_range) into search cdata
            conseq_cdata = dict(cdata)
            if consequence.get("context_must"):
                conseq_cdata["context_must"] = consequence["context_must"]
            if consequence.get("typical_range"):
                conseq_cdata["typical_range"] = consequence["typical_range"]
            val = self._search_phase1_params(consequence_param, conseq_cdata, phase1_data)
            values[consequence_param] = {
                "value": val,
                "source": "phase1_data" if val is not None else "user_input_required",
            }

        has_any = any(v.get("value") is not None for v in values.values())
        status = "extracted" if has_any else "user_input_required"
        return {"status": status, "values": values}

    def _extract_data_derived(self, cname: str, cdata: dict, phase1_data: dict) -> dict:
        derivation = cdata.get("derivation", {})
        method = derivation.get("method", "")

        if method == "compute_big_m":
            max_time = phase1_data.get("max_time", None)
            if max_time is not None:
                m_value = max(1440, int(max_time * 1.5))
                return {
                    "status": "auto_computed",
                    "values": {"M": {"value": m_value, "source": "auto_computed"}},
                }

        if method == "generate_duty_candidates":
            trip_count = phase1_data.get("trip_count", 0)
            if trip_count > 0:
                user_params = cdata.get("user_adjustable_params", {})
                default_mult = user_params.get("candidate_multiplier", {}).get("default", 1.5)
                candidate_size = int(trip_count * default_mult)
                return {
                    "status": "auto_computed",
                    "values": {
                        "candidate_set_size": {"value": candidate_size, "source": "auto_computed"},
                        "candidate_multiplier": {"value": default_mult, "source": "default_adjustable"},
                    },
                }

        return {
            "status": "computed_in_phase2",
            "values": {},
            "computation_phase": "semantic_normalization",
        }

    # ── Phase 1 데이터 로드 ──
    def _load_phase1_data(self, project_id: str) -> dict:
        """Phase 1 결과 파일에서 제약조건 추출에 필요한 정보를 수집"""
        phase1_dir = _get_safe_dir(project_id) / "phase1"
        result = {"has_trips": False, "trip_count": 0, "params_raw": [], "columns": set()}

        # trips
        trips_file = phase1_dir / "timetable_rows.csv"
        if trips_file.exists():
            try:
                df = pd.read_csv(str(trips_file))
                result["has_trips"] = True
                result["trip_count"] = len(df)
                result["columns"].update(df.columns.tolist())
                if "trip_arr_time" in df.columns:
                    result["max_time"] = float(df["trip_arr_time"].max())
                if "trip_dep_time" in df.columns:
                    result["min_time"] = float(df["trip_dep_time"].min())
                if "trip_duration" in df.columns:
                    result["avg_duration"] = float(df["trip_duration"].mean())
                if "dep_station" in df.columns and "arr_station" in df.columns:
                    result["stations"] = sorted(
                        set(df["dep_station"].tolist() + df["arr_station"].tolist())
                    )
            except Exception as e:
                logger.warning(f"Phase1 trips read failed: {e}")

        # parameters_raw
        params_file = phase1_dir / "parameters_raw.csv"
        if params_file.exists():
            try:
                pdf = pd.read_csv(str(params_file))
                result["params_raw"] = pdf.to_dict("records")
            except Exception as e:
                logger.warning(f"Phase1 params read failed: {e}")

        # structure_report
        report_file = phase1_dir / "structure_report.json"
        if report_file.exists():
            try:
                with open(str(report_file), "r", encoding="utf-8") as f:
                    result["structure_report"] = json.load(f)
            except Exception:
                pass

        return result

    def _search_phase1_params(self, param_name: str, cdata: dict, phase1_data: dict):
        """Phase 1 파라미터에서 제약조건 값을 검색 (v3 - context_must + typical_range)
        
        매칭 전략:
        1. param_name 직접 매칭 (한글 이름)
        2. 준비/정리 구분
        3. detection_hints 키워드 매칭
        4. context_must: 핵심 키워드가 context에 반드시 포함 (AND)
        5. typical_range 범위 내 보너스, 범위 밖 페널티
        6. 최대/최소/평균 context 힌트
        7. 동점 후보 시 operator 기반 선택
        """
        params_raw = phase1_data.get("params_raw", [])
        if not params_raw:
            return None

        # detection_hints에서 키워드 수집
        # sub-param에 독립 hints가 있으면 우선 사용
        hints = cdata.get("detection_hints", {})
        sub_hints = None
        for pid, pinfo in (cdata.get("parameters") or {}).items():
            if pid == param_name and isinstance(pinfo, dict):
                sub_hints = pinfo.get("detection_hints", {})
                break
        if sub_hints:
            hints = sub_hints
        all_keywords = []
        if isinstance(hints, dict):
            for lang_keywords in hints.values():
                if isinstance(lang_keywords, list):
                    all_keywords.extend(kw.lower() for kw in lang_keywords)
        elif isinstance(hints, list):
            all_keywords.extend(h.lower() for h in hints)

        # typical_range
        typical_range = cdata.get("typical_range", [])
        # compound params에서 개별 typical_range
        for pid, pinfo in (cdata.get("parameters") or {}).items():
            if pid == param_name and isinstance(pinfo, dict):
                typical_range = pinfo.get("typical_range", typical_range)
                break
        # conditional trigger/consequence의 typical_range
        trigger = cdata.get("trigger") or {}
        if trigger.get("threshold_param") == param_name:
            typical_range = trigger.get("typical_threshold_range", typical_range)
        consequence = cdata.get("consequence") or {}
        if consequence.get("parameter") == param_name:
            typical_range = consequence.get("typical_range", typical_range)

        # operator/role 힌트
        operator = cdata.get("operator", "")
        role = ""
        for pid, pinfo in (cdata.get("parameters") or {}).items():
            if pid == param_name and isinstance(pinfo, dict):
                role = pinfo.get("role", "")
                break
        is_max = operator in ("<=", "<") or "max" in param_name.lower() or "upper" in role
        is_min = operator in (">=", ">") or "min" in param_name.lower() or "최소" in param_name
        is_avg = "avg" in param_name.lower() or "average" in param_name.lower()

        # 준비/정리 구분
        is_prep = any(w in param_name.lower() for w in ["prep", "준비"])
        is_cleanup = any(w in param_name.lower() for w in ["cleanup", "정리"])

        # context_must: cdata에 명시된 것을 우선 사용, 없으면 keywords에서 자동 생성
        context_must = cdata.get("context_must", None)
        if not context_must:
            context_must = [kw for kw in all_keywords if len(kw) >= 2
                            and kw not in ("최대", "최소", "시간", "제한", "limit")]

        candidates = []

        for p in params_raw:
            pname = str(p.get("param_name", "")).lower()
            semantic_id = str(p.get("semantic_id", "")).lower()
            context = str(p.get("context", "")).lower()
            value = p.get("value")

            if value is None:
                continue

            # 숫자 변환
            try:
                num_val = float(value)
            except (ValueError, TypeError):
                continue

            # semantic_id 직접 매칭 → 즉시 반환 (최우선)
            if semantic_id and semantic_id == param_name.lower():
                logger.info(
                    f"Phase1 exact match by semantic_id: {param_name} = {num_val}"
                    f" (param_name={pname}, semantic_id={semantic_id})"
                )
                return num_val

            # context_must 필터: 핵심 키워드 중 하나라도 context에 있어야 함
            if context_must and not any(cm in context or cm in pname for cm in context_must):
                continue

            score = 0

            # 1. param_name 직접 매칭
            if param_name.lower() == pname or param_name.lower() in pname:
                score += 5

            # 2. 준비/정리 구분
            if is_prep and "준비" in pname:
                score += 4
            elif is_cleanup and "정리" in pname:
                score += 4
            elif is_prep and "정리" in pname and "준비" not in pname:
                continue
            elif is_cleanup and "준비" in pname and "정리" not in pname:
                continue

            # 3. detection_hints 키워드 매칭
            for kw in all_keywords:
                if kw in context:
                    score += 1.5
                elif kw in pname:
                    score += 1

            # 4. 최대/최소/평균 context 보너스
            if is_max:
                if any(w in context for w in ["이내", "이하"]):
                    score += 3
                elif "최대" in context and "최대한" not in context:
                    score += 2
                if "평균" in context:
                    score -= 2
            if is_min:
                if any(w in context for w in ["이상", "최소"]):
                    score += 3
                elif "하한" in context:
                    score += 2
                if "평균" in context:
                    score -= 2
            if is_avg and any(w in context for w in ["평균", "avg"]):
                score += 3

            # 5. typical_range 보너스/페널티
            if typical_range and len(typical_range) == 2:
                lo, hi = typical_range[0], typical_range[1]
                if lo <= num_val <= hi:
                    score += 3
                elif lo * 0.5 <= num_val <= hi * 2:
                    score += 1
                else:
                    score -= 2

            if score >= 1.5:
                candidates.append({"value": num_val, "score": score, "pname": pname})

        if not candidates:
            return None

        # 최적 선택
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top_score = candidates[0]["score"]
        top_candidates = [c for c in candidates if c["score"] >= top_score * 0.9]

        if len(top_candidates) == 1:
            logger.info(
                f"Phase1 match: {param_name} = {top_candidates[0]['value']}"
                f" (score={top_candidates[0]['score']}, from={top_candidates[0]['pname']})"
            )
            return top_candidates[0]["value"]

        # 동점 시 최대/최소/평균 선택
        values = [c["value"] for c in top_candidates]
        if is_max:
            result = max(values)
        elif is_min:
            result = min(values)
        elif is_avg:
            result = round(sum(values) / len(values), 2)
        else:
            result = top_candidates[0]["value"]

        logger.info(
            f"Phase1 match: {param_name} = {result}"
            f" ({len(top_candidates)} candidates, is_max={is_max}, is_min={is_min})"
        )
        return result


    def _collect_parameters(self, state, project_id: str, dk, constraints: dict) -> dict:
        """Phase 1 parameters.csv + 제약조건 values 종합 수집"""
        logger.info(f"_collect_parameters called: project_id={project_id}")
        import csv as _csv
        import os as _os
        import re as _re
        parameters = {}

        # ── 1단계: Phase 1 parameters.csv에서 semantic_id 기반 수집 ──
        _base = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
        norm_dir = _os.path.join(_base, "uploads", str(project_id), "normalized")
        csv_path = _os.path.join(norm_dir, "parameters.csv")
        logger.info(f"_collect_parameters: csv_path={csv_path}, exists={_os.path.exists(csv_path)}")
        if _os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = _csv.DictReader(f)
                    for row in reader:
                        sid = (row.get("semantic_id") or "").strip()
                        pname = (row.get("param_name") or "").strip()
                        pid = sid if sid and sid != pname else None
                        if not pid:
                            continue
                        # 중복 접미사(_2, _3 등)는 첫 번째 값만 사용
                        base_id = _re.sub(r"_(2|3|4|5|avg|min|max)$", "", pid)
                        if base_id in parameters:
                            continue
                        val_str = (row.get("value") or "").strip()
                        try:
                            val = float(val_str)
                        except (ValueError, TypeError):
                            val = val_str if val_str else None
                        unit = (row.get("unit") or "").strip()
                        parameters[pid] = {
                            "value": val,
                            "unit": unit or "minutes",
                            "source": "parameters.csv",
                        }
                logger.info(f"_collect_parameters: {len(parameters)} params from parameters.csv")
            except Exception as e:
                logger.warning(f"_collect_parameters: CSV read failed: {e}")

        # ── 2단계: 제약조건 values에서 추가 수집 ──
        for category in ["hard", "soft"]:
            for cname, cinfo in constraints.get(category, {}).items():
                for pname, pval in cinfo.get("values", {}).items():
                    if pname not in parameters:
                        parameters[pname] = pval

        # ── 3단계: reference_ranges 참고 범위 ──
        if dk:
            sub_domain = self._detect_sub_domain(state, dk)
            if sub_domain:
                for pname in list(parameters.keys()):
                    ref = dk.get_reference_range(sub_domain, pname)
                    if ref and isinstance(parameters[pname], dict) and parameters[pname].get("value") is None:
                        parameters[pname]["reference_range"] = ref.get("range")
                        parameters[pname]["note"] = ref.get("note", "")

        return parameters

    def _detect_sub_domain(self, state, dk) -> Optional[str]:
        search_text = ""
        if state.uploaded_files:
            search_text += " ".join(str(f).lower() for f in state.uploaded_files)
        if state.csv_summary:
            search_text += " " + state.csv_summary.lower()

        for sub_key, sub_data in dk.sub_domains.items():
            keywords = sub_data.get("detection_keywords", [])
            if any(kw.lower() in search_text for kw in keywords):
                return sub_key
        return None

    # ──────────────────────────────────────
    # 응답 포맷팅
    # ──────────────────────────────────────
    def _format_proposal(self, state, dk, proposal: dict) -> str:
        lines = ["## 문제 정의 제안\n"]

        # 1. 문제 유형
        stage = proposal.get("stage", "")
        stage_info = self.taxonomy.get("stages", {}).get(stage, {})
        stage_ko = stage_info.get("name_ko", stage)
        topology = proposal.get("topology")

        lines.append("### 1. 문제 유형")
        lines.append(f"- **단계**: {stage_ko}")
        if topology:
            topo_info = dk.network_topologies.get(topology, {}) if dk else {}
            topo_ko = topo_info.get("name_ko", topology)
            lines.append(f"- **네트워크 유형**: {topo_ko}")
        lines.append("")

        # 2. 목적함수
        obj = proposal.get("objective", {})
        lines.append("### 2. 목적함수")
        lines.append(f"- **방향**: {obj.get('type', 'minimize')}")
        lines.append(f"- **대상**: {obj.get('description', '')}")
        alts = obj.get("alternatives", [])
        if alts:
            alt_texts = [a.get("description", a.get("target", "")) for a in alts]
            lines.append(f"- **대안**: {', '.join(alt_texts)}")
        lines.append("")

        # 3. 제약조건 (타입별 그룹)
        lines.append("### 3. 제약조건\n")

        hard = proposal.get("hard_constraints", {})
        if hard:
            # 상태별 분류
            confirmed = {k: v for k, v in hard.items() if v.get("status") in ("confirmed", "extracted", "auto_computed")}
            partial = {k: v for k, v in hard.items() if v.get("status") == "partial"}
            # auto_model_variable은 입력 필요에서 제외
            needs_input = {}
            for k, v in hard.items():
                if v.get("status") != "user_input_required":
                    continue
                # 하위 값 중 auto_model_variable만 있으면 skip
                vals = v.get("values", {})
                has_real_missing = any(
                    sv.get("source") == "user_input_required" and sv.get("value") is None
                    for sv in vals.values()
                )
                if has_real_missing:
                    needs_input[k] = v
            computed_later = {k: v for k, v in hard.items() if v.get("status") in ("computed_in_phase2",)}

            if confirmed:
                lines.append("**✅ 확인된 제약 (Hard):**")
                for cname, cdata in confirmed.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    changeable = dk.is_category_changeable(cname) if dk else False
                    tag = " [변경가능]" if changeable else ""
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}{tag}")
                lines.append("")

            if partial:
                lines.append("**⚠️ 일부 확인된 제약 (Hard):**")
                for cname, cdata in partial.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    changeable = dk.is_category_changeable(cname) if dk else False
                    tag = " [변경가능]" if changeable else ""
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}{tag}")
                lines.append("")

            if needs_input:
                lines.append("**❓ 입력 필요 (Hard):**")
                for cname, cdata in needs_input.items():
                    name_ko = cdata.get("name_ko", cname)
                    desc = cdata.get("description", "")
                    lines.append(f"- **{name_ko}**: {desc}")
                    for pname, pval in cdata.get("values", {}).items():
                        ref = pval.get("reference_range")
                        ref_default = pval.get("reference_default")
                        if ref and ref_default is not None:
                            lines.append(f"  - `{pname}` = ??? (참고 범위: {ref}, 기본값: {ref_default})")
                        elif ref:
                            lines.append(f"  - `{pname}` = ??? (참고 범위: {ref})")
                        elif ref_default is not None:
                            lines.append(f"  - `{pname}` = ??? (기본값: {ref_default})")
                        else:
                            lines.append(f"  - `{pname}` = ???")
                lines.append("")

            if computed_later:
                lines.append("**🔄 자동 계산 예정 (Phase 2):**")
                for cname, cdata in computed_later.items():
                    name_ko = cdata.get("name_ko", cname)
                    lines.append(f"- **{name_ko}**: 데이터 정규화 후 자동 계산")
                lines.append("")

        soft = proposal.get("soft_constraints", {})
        if soft:
            lines.append("**선택 제약 (Soft):**")
            for cname, cdata in soft.items():
                name_ko = cdata.get("name_ko", cname)
                weight = cdata.get("weight", 0)
                changeable = dk.is_category_changeable(cname) if dk else False
                tag = " [변경가능]" if changeable else ""
                lines.append(f"- **{name_ko}**: {cdata.get('description','')} (가중치: {weight}){tag}")
            lines.append("")

        # ── 카테고리 변경 안내 ──
        if hard or soft:
            lines.append("---")
            lines.append("💡 **제약조건 카테고리 변경:**")
            lines.append("- [변경가능] 표시된 제약은 Hard↔Soft 변경이 가능합니다.")
            lines.append("- 예시: mandatory_break soft로 변경 또는 max_total_stay_time hard로 변경")
            lines.append("")
        # 4. 파라미터 요약
        params = proposal.get("parameters", {})
        if params:
            data_params = {k: v for k, v in params.items() if v.get("source") in ("phase1_data", "auto_computed")}
            missing_params = {k: v for k, v in params.items() if v.get("source") == "user_input_required"}

            if data_params:
                lines.append("### 4. 파라미터 (데이터 추출)")
                for pname, pinfo in data_params.items():
                    lines.append(f"- {pname}: **{pinfo.get('value')}**")
                lines.append("")

            if missing_params:
                lines.append("### 5. 입력 필요 파라미터")
                for pname, pinfo in missing_params.items():
                    ref = pinfo.get("reference_range")
                    if ref:
                        lines.append(f"- {pname}: ??? (참고 범위: {ref})")
                    else:
                        lines.append(f"- {pname}: ???")
                lines.append("")

        lines.append("---")
        lines.append("위 내용을 확인하고 **확인**, **수정**, 또는 **다시 분석**을 입력해주세요.")
        lines.append("파라미터 입력: `파라미터명 = 값` 형식")

        return "\n".join(lines)

    def _format_values(self, values: dict) -> str:
        parts = []
        for pname, pval in values.items():
            v = pval.get("value")
            if v is not None:
                parts.append(f"{pname}={v}")
            else:
                parts.append(f"{pname}=???")
        return ", ".join(parts) if parts else "구조적 제약"

    # ──────────────────────────────────────
    # 패널 확정 이벤트 처리 (problem_definition_confirm)
    # ──────────────────────────────────────
    async def _handle_pd_confirm(
        self, session: CrewSession, project_id: str, event_data: Dict
    ) -> Dict:
        state = session.state
        pd = state.problem_definition
        if not pd:
            pd = {}

        constraint_changes = event_data.get("constraint_changes", [])
        new_constraints = event_data.get("new_constraints", [])

        # ── 새 제약조건 추가 (템플릿 기반) ──
        added_lines = []
        if new_constraints:
            dk = self._load_domain(state)
            for nc in new_constraints:
                cname = nc.get("name")
                category = nc.get("category", "hard")
                if not cname:
                    continue
                # 이미 존재하면 스킵
                if cname in pd.get("hard_constraints", {}) or cname in pd.get("soft_constraints", {}):
                    continue
                # constraints.yaml에서 메타데이터 가져오기
                template = dk.get_constraint(cname) if dk else None
                if template:
                    cdata = {
                        "description": template.get("description", ""),
                        "name_ko": template.get("description", cname),
                        "expression_template": template.get("expression_template", ""),
                        "for_each": template.get("for_each", ""),
                        "parameters": template.get("parameters", []),
                        "fixed": template.get("fixed_category", False),
                        "changeable": not template.get("fixed_category", False),
                        "detection_hints": template.get("detection_hints", []),
                        "penalty_weight": template.get("penalty_weight", 10),
                    }
                else:
                    cdata = {
                        "description": nc.get("description", ""),
                        "name_ko": nc.get("description", cname),
                        "fixed": False,
                        "changeable": True,
                    }
                target_key = f"{category}_constraints"
                pd.setdefault(target_key, {})[cname] = cdata
                added_lines.append(f"  - **{cdata['name_ko']}** ({category.upper()})")

                # 제약에 필요한 파라미터도 추가
                if template and template.get("parameters"):
                    params = pd.get("parameters", {})
                    for pname in template["parameters"]:
                        if pname not in params:
                            # reference_ranges에서 기본값 가져오기
                            ref_val = None
                            typical = template.get("typical_range")
                            if typical and len(typical) >= 2:
                                ref_val = typical[1]  # 상한을 기본값으로
                            params[pname] = {
                                "value": ref_val,
                                "source": "reference_default" if ref_val else "user_input_required",
                            }
                    pd["parameters"] = params

        # 제약조건 변경 적용
        changed_lines = []
        for change in constraint_changes:
            cname = change.get("name")
            to_cat = change.get("to")
            if not cname or not to_cat:
                continue
            from_cat = "soft" if to_cat == "hard" else "hard"
            from_key = f"{from_cat}_constraints"
            to_key = f"{to_cat}_constraints"
            if cname in pd.get(from_key, {}):
                moved = pd[from_key].pop(cname)
                pd.setdefault(to_key, {})[cname] = moved
                direction = "Hard → Soft" if to_cat == "soft" else "Soft → Hard"
                changed_lines.append(f"  - **{cname}**: {direction}")

        # 문제 정의 확정
        state.problem_definition = pd
        state.problem_defined = True
        state.confirmed_problem = pd
        state.constraints_confirmed = True
        state.confirmed_constraints = {
            "hard": pd.get("hard_constraints", {}),
            "soft": pd.get("soft_constraints", {}),
        }
        save_session_state(project_id, state)

        change_text = ""
        if changed_lines:
            change_text = f"\n\n**변경된 제약조건 ({len(changed_lines)}개):**\n" + "\n".join(changed_lines)
        if added_lines:
            change_text += f"\n\n**추가된 제약조건 ({len(added_lines)}개):**\n" + "\n".join(added_lines)

        return {
            "type": "problem_definition",
            "text": f"✅ 문제 정의가 확정되었습니다.{change_text}\n\n데이터 정규화 단계로 진행합니다.",
            "data": {
                "view_mode": "problem_defined",
                "proposal": pd,
                "confirmed_problem": pd,
                "auto_next": "data_normalization",
            },
            "options": [],
        }

    # ──────────────────────────────────────
    # 사용자 응답 처리
    # ──────────────────────────────────────
    async def _handle_user_response(
        self, model, session: CrewSession, project_id: str, message: str
    ) -> Dict:
        state = session.state
        keywords = self.prompt_config.get("confirmation_keywords", {})
        msg_lower = message.strip().lower()

        positive = [k.lower() for k in keywords.get("positive", [])]
        modify = [k.lower() for k in keywords.get("modify", [])]
        restart = [k.lower() for k in keywords.get("restart", [])]

        # 확인
        if any(kw in msg_lower for kw in positive):
            state.problem_defined = True
            state.confirmed_problem = state.problem_definition
            state.constraints_confirmed = True
            state.confirmed_constraints = {
                "hard": state.problem_definition.get("hard_constraints", {}),
                "soft": state.problem_definition.get("soft_constraints", {}),
            }
            save_session_state(project_id, state)

            return {
                "type": "problem_definition",
                "text": (
                    "**문제 정의가 확정되었습니다.**\n\n"
                    "데이터 정규화를 자동으로 진행합니다..."
                ),
                "data": {
                    "view_mode": "problem_defined",
                    "confirmed_problem": state.confirmed_problem,
                    "agent_status": "problem_defined",
                    "auto_next": "data_normalization",
                },
                "options": [],
            }

        # ── 목적함수 변경 early detection (before modify keyword catch) ──
        _is_objective_change = bool(
            state.problem_definition and
            ("목적함수" in message or "objective" in msg_lower)
        )

        # ── 제약조건 카테고리 변경 early detection (before modify keyword catch) ──
        _cat_early = re.search(r'(\w+)\s+(?:를\s*|을\s*)?(?:로\s*)?(hard|soft)(?:로)?(?:\s*변경|\s*전환|\s*바꿔)', message, re.IGNORECASE)
        if not _cat_early:
            _cat_early = re.search(r'(?:change|move|switch|set)\s+(\w+)\s+(?:to\s+)?(hard|soft)', message, re.IGNORECASE)
        _is_category_change = bool(_cat_early and state.problem_definition)

        # 수정 요청 (목적함수 변경, 카테고리 변경은 전용 핸들러로 처리)
        if not _is_category_change and not _is_objective_change and any(kw in msg_lower for kw in modify):
            return {
                "type": "problem_definition",
                "text": (
                    "수정할 항목을 알려주세요. 예시:\n\n"
                    "- 목적함수를 [목적함수명]으로 변경\n"
                    "- [파라미터명] = [값]\n"
                    "- [제약조건명] 제거\n"
                    "- [제약조건명] soft로 변경 (Hard→Soft)\n"
                    "- [제약조건명] hard로 변경 (Soft→Hard)\n"
                ),
                "data": {
                    "view_mode": "problem_definition",
                    "proposal": state.problem_definition,
                    "agent_status": "modification_pending",
                },
                "options": [],
            }

        # 재시작
        if any(kw in msg_lower for kw in restart):
            state.problem_definition = None
            state.problem_definition_proposed = False
            state.problem_defined = False
            state.confirmed_problem = None
            state.constraints_confirmed = False
            state.confirmed_constraints = None
            save_session_state(project_id, state)

            return {
                "type": "info",
                "text": "문제 정의를 초기화했습니다. 다시 분석을 시작합니다.",
                "data": {"agent_status": "reset"},
                "options": [
                    {"label": "분석 시작", "action": "send", "message": "분석 시작해줘"},
                ],
            }

        # ── 목적함수 변경 ──
        import re as _re
        obj_pattern = _re.compile(
            r"목적함수[를을]?\s*(.+?)(?:로|으로)\s*(?:변경|바꿔|바꾸|수정)|"
            r"(.+?)(?:로|으로)\s*목적함수[를을]?\s*(?:변경|바꿔|바꾸|수정)|"
            r"목적함수[를을]?\s*(.+?)(?:을|를)?\s*(?:변경|바꿔|바꾸|수정)|"
            r"objective\s+(?:to\s+)?(\w+)",
            _re.IGNORECASE
        )
        obj_match = obj_pattern.search(message)
        if obj_match and state.problem_definition:
            requested = (obj_match.group(1) or obj_match.group(2) or obj_match.group(3) or obj_match.group(4) or "").strip()
            if not requested:
                obj_match = None  # 빈 문자열이면 매칭 실패 처리

            # dk에서 objectives 로드
            dk = self._load_domain(state)
            import yaml as _yaml
            domain_name = state.problem_definition.get("domain", "railway")
            _constraints_path = f"knowledge/domains/{domain_name}/constraints.yaml"
            try:
                with open(_constraints_path, encoding="utf-8") as _f:
                    _cdata = _yaml.safe_load(_f)
                objectives_map = _cdata.get("objectives", {})
            except Exception:
                objectives_map = {}

            # 매칭: 이름 또는 description_ko에서 유사도 스코어링
            matched_obj = None
            best_score = 0
            for oname, odata in objectives_map.items():
                desc_ko = odata.get("description_ko", "")
                desc_en = odata.get("description", "")
                score = 0
                # 정확히 일치
                if requested == desc_ko or requested == oname:
                    score = 100
                elif requested == desc_en:
                    score = 100
                # desc_ko가 requested에 완전 포함 (사용자가 더 긴 텍스트 입력)
                elif desc_ko and desc_ko in requested:
                    score = 80 + len(desc_ko)
                elif requested in desc_ko:
                    score = 60 + len(requested)
                elif requested in oname:
                    score = 50 + len(requested)
                # 단어 겹침 비교
                else:
                    req_words = set(requested.split())
                    ko_words = set(desc_ko.split()) if desc_ko else set()
                    overlap = req_words & ko_words
                    if overlap:
                        score = 30 + len(overlap) * 10
                if score > best_score:
                    best_score = score
                    matched_obj = (oname, odata)

            if matched_obj:
                oname, odata = matched_obj

                # ── 목적함수 외 추가 지시사항 추출 ──
                # "업무량 균형 최적화로 목적함수 변경 단 주간 근무 32명, 야간 근무 13명 조건으로"
                # → 목적함수 변경 부분을 제거하고 나머지를 추가 지시사항으로 저장
                _extra_instructions = ""
                if obj_match:
                    _matched_span = obj_match.group(0)
                    _remainder = message.replace(_matched_span, "").strip()
                    # "단", "그리고", "조건으로" 등 접속사 제거
                    _remainder = _re.sub(r'^[\s,단\.그리고]+', '', _remainder).strip()
                    if len(_remainder) >= 4:  # 의미 있는 텍스트만
                        _extra_instructions = _remainder

                # ── 경고 게이트: objective_changing 플래그 확인 ──
                if not getattr(state, 'objective_changing', False):
                    # 첫 번째 요청 → 경고 메시지 + 확인 요청
                    state.objective_changing = True
                    state._pending_objective = {"name": oname, "data": odata}
                    state._pending_extra_instructions = _extra_instructions
                    save_session_state(project_id, state)

                    old_obj = state.problem_definition.get("objective", {})
                    old_desc = old_obj.get("description", old_obj.get("description_ko", "현재 목적함수"))
                    new_desc = odata.get("description_ko", odata["description"])

                    promote_info = ""
                    promote_list = odata.get("promote_to_hard", [])
                    if promote_list:
                        promote_info = f"\n- 자동 Hard 승격 제약: {', '.join(promote_list)}"

                    extra_note = ""
                    if _extra_instructions:
                        extra_note = f"\n\n📋 **추가 조건 (목적함수 변경 후 적용):**\n> {_extra_instructions}"

                    return {
                        "type": "problem_definition",
                        "text": (
                            f"⚠️ **목적함수 변경 확인**\n\n"
                            f"- 현재: {old_desc}\n"
                            f"- 변경: **{new_desc}**\n"
                            f"{promote_info}\n\n"
                            f"**목적함수를 변경하면 제약조건이 새로 구성됩니다.**\n"
                            f"현재 수정한 제약조건 편집 내용은 초기화됩니다."
                            f"{extra_note}\n\n"
                            f"계속하시겠습니까?"
                        ),
                        "data": {
                            "view_mode": "problem_definition",
                            "proposal": state.problem_definition,
                            "agent_status": "objective_change_warning",
                            "pending_objective": oname,
                        },
                        "options": [
                            {"label": "✅ 계속 변경", "action": "send",
                             "message": f"목적함수를 {new_desc}으로 변경"},
                            {"label": "❌ 취소", "action": "send", "message": "취소"},
                        ],
                    }

                # ── 두 번째 요청 (확인됨) → 실제 변경 + 제약조건 재구성 ──
                _extra_instr = getattr(state, '_pending_extra_instructions', '') or ''
                state.objective_changing = False
                state._pending_objective = None
                state._pending_extra_instructions = None
                old_obj = state.problem_definition.get("objective", {})
                old_desc = old_obj.get("description", "알 수 없음")

                # 목적함수 업데이트
                state.problem_definition["objective"] = {
                    "type": odata["type"],
                    "target": oname,
                    "description": odata["description"],
                    "description_ko": odata.get("description_ko", odata["description"]),
                    "expression": odata.get("expression", ""),
                    "alternatives": [
                        {"target": k, "description": v.get("description_ko", v["description"])}
                        for k, v in objectives_map.items() if k != oname
                    ],
                }

                # ── 제약조건 재구성 ──
                detected_data_types = set(state.problem_definition.get("detected_data_types", []))
                topology = state.problem_definition.get("topology")
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    new_constraints = loop.run_until_complete(
                        self._determine_constraints_phased(
                            None, state, project_id, dk,
                            detected_data_types, topology
                        )
                    ) if not asyncio.get_event_loop().is_running() else (
                        await self._determine_constraints_phased(
                            None, state, project_id, dk,
                            detected_data_types, topology
                        )
                    )
                except Exception:
                    new_constraints = await self._determine_constraints_phased(
                        None, state, project_id, dk,
                        detected_data_types, topology
                    )

                state.problem_definition["hard_constraints"] = new_constraints.get("hard", {})
                state.problem_definition["soft_constraints"] = new_constraints.get("soft", {})

                # promote_to_hard 처리
                changes = []
                promote_list = odata.get("promote_to_hard", [])
                for cname in promote_list:
                    if cname in state.problem_definition.get("soft_constraints", {}):
                        moved = state.problem_definition["soft_constraints"].pop(cname)
                        state.problem_definition["hard_constraints"][cname] = moved
                        changes.append(f"  - **{cname}**: Soft → Hard (목적함수 연동)")
                        if dk:
                            dk.move_constraint(cname, "hard", force=True)

                # 파라미터 재수집 (제약조건 변경에 따라 필요 파라미터도 달라짐)
                new_all_constraints = {
                    "hard": state.problem_definition.get("hard_constraints", {}),
                    "soft": state.problem_definition.get("soft_constraints", {}),
                }
                state.problem_definition["parameters"] = self._collect_parameters(
                    state, project_id, dk, new_all_constraints
                )

                # 확정 상태 초기화 (재확인 필요)
                state.constraints_confirmed = False
                state.confirmed_constraints = None
                save_session_state(project_id, state)

                hard_count = len(state.problem_definition.get("hard_constraints", {}))
                soft_count = len(state.problem_definition.get("soft_constraints", {}))

                change_text = ""
                if changes:
                    change_text = "\n\n**자동 연동 변경:**\n" + "\n".join(changes)

                # ── 추가 지시사항이 있으면 LLM Smart Apply로 처리 ──
                extra_applied_text = ""
                if _extra_instr:
                    try:
                        extra_result = await self._llm_smart_apply(
                            model, state, project_id, _extra_instr
                        )
                        # _llm_smart_apply가 state를 직접 수정하므로 결과 텍스트만 추출
                        extra_text = extra_result.get("text", "")
                        if extra_text:
                            extra_applied_text = f"\n\n**추가 조건 적용:**\n{extra_text}"
                        # 업데이트된 proposal 사용
                        save_session_state(project_id, state)
                    except Exception as e:
                        logger.warning(f"Extra instructions apply failed: {e}")
                        extra_applied_text = f"\n\n⚠️ 추가 조건 적용 실패: 확인 후 별도로 입력해주세요.\n> {_extra_instr}"

                return {
                    "type": "problem_definition",
                    "text": (
                        f"✅ 목적함수를 변경하고 제약조건을 재구성했습니다.\n\n"
                        f"- 이전: {old_desc}\n"
                        f"- 변경: **{odata.get('description_ko', odata['description'])}**\n"
                        f"- 수식: {odata.get('expression', '')}\n"
                        f"- 재구성 결과: Hard {hard_count}개, Soft {soft_count}개"
                        f"{change_text}"
                        f"{extra_applied_text}\n\n"
                        f"아래에서 제약조건을 확인하고 필요시 수정해주세요."
                    ),
                    "data": {
                        "view_mode": "problem_definition",
                        "proposal": state.problem_definition,
                        "agent_status": "objective_changed_constraints_rebuilt",
                    },
                    "options": [
                        {"label": "✅ 확인", "action": "send", "message": "확인"},
                        {"label": "✏️ 제약조건 수정", "action": "send", "message": "수정"},
                    ],
                }

            else:
                # 매칭 실패
                obj_list = "\n".join([
                    f"- **{k}**: {v.get('description_ko', v['description'])}"
                    for k, v in objectives_map.items()
                ])
                return {
                    "type": "problem_definition",
                    "text": (
                        f"'{requested}'에 해당하는 목적함수를 찾을 수 없습니다.\n\n"
                        f"**사용 가능한 목적함수:**\n{obj_list}\n\n"
                        f"위 이름으로 다시 입력해주세요."
                    ),
                    "data": {"agent_status": "objective_change_failed"},
                    "options": [
                        {"label": k, "action": "send",
                         "message": f"목적함수를 {v.get('description_ko', k)}로 변경"}
                        for k, v in list(objectives_map.items())[:4]
                    ],
                }

        # ── 목적함수 변경 fallback: early detection 매치했으나 regex 미매치 시 ──
        if _is_objective_change and state.problem_definition:
            dk = self._load_domain(state)
            import yaml as _yaml2
            domain_name = state.problem_definition.get("domain", "railway")
            _constraints_path2 = f"knowledge/domains/{domain_name}/constraints.yaml"
            try:
                with open(_constraints_path2, encoding="utf-8") as _f2:
                    _cdata2 = _yaml2.safe_load(_f2)
                objectives_map2 = _cdata2.get("objectives", {})
            except Exception:
                objectives_map2 = {}

            if objectives_map2:
                obj_list = "\n".join([
                    f"- **{k}**: {v.get('description_ko', v['description'])}"
                    for k, v in objectives_map2.items()
                ])
                current_obj = state.problem_definition.get("objective", {})
                current_desc = current_obj.get("description_ko", current_obj.get("description", ""))
                return {
                    "type": "problem_definition",
                    "text": (
                        f"현재 목적함수: **{current_desc}**\n\n"
                        f"**변경 가능한 목적함수:**\n{obj_list}\n\n"
                        f"변경할 목적함수를 선택해주세요."
                    ),
                    "data": {
                        "view_mode": "problem_definition",
                        "proposal": state.problem_definition,
                        "agent_status": "objective_change_selection",
                    },
                    "options": [
                        {"label": v.get("description_ko", k), "action": "send",
                         "message": f"목적함수를 {v.get('description_ko', k)}로 변경"}
                        for k, v in list(objectives_map2.items())[:4]
                    ],
                }

        # ── 취소 처리 (objective_changing 중일 때) ──
        if getattr(state, 'objective_changing', False) and ('취소' in msg_lower or 'cancel' in msg_lower):
            state.objective_changing = False
            state._pending_objective = None
            save_session_state(project_id, state)
            return {
                "type": "problem_definition",
                "text": "목적함수 변경을 취소했습니다. 현재 설정을 유지합니다.",
                "data": {
                    "proposal": state.problem_definition,
                    "agent_status": "objective_change_cancelled",
                },
                "options": [
                    {"label": "✅ 확인", "action": "send", "message": "확인"},
                    {"label": "✏️ 수정", "action": "send", "message": "수정"},
                ],
            }

        # ── 제약조건 카테고리 변경 (hard↔soft) ──
        category_pattern = re.compile(
            r"(\w+)\s+(?:를\s*|을\s*)?(?:로\s*)?(hard|soft)(?:로)?(?:\s*변경|\s*전환|\s*바꿔|\s*바꾸)",
            re.IGNORECASE
        )
        cat_match = category_pattern.search(message)
        if not cat_match:
            # 영어 패턴: "change max_total_stay_time to hard"
            cat_pattern_en = re.compile(
                r"(?:change|move|switch|set)\s+(\w+)\s+(?:to\s+)?(hard|soft)",
                re.IGNORECASE
            )
            cat_match = cat_pattern_en.search(message)

        if cat_match and state.problem_definition:
            cname = cat_match.group(1)
            to_cat = cat_match.group(2).lower()

            # dk 로드
            dk = self._load_domain(state)

            # pending_category_change가 있으면 사용자가 경고에 확인한 것
            pending = getattr(state, '_pending_category_change', None)
            if pending and pending.get("constraint") == cname and pending.get("to") == to_cat:
                # 사용자가 이전 경고에 대해 다시 같은 명령 → force
                force = True
                state._pending_category_change = None
            else:
                force = False

            result = dk.move_constraint(cname, to_cat, force=force)

            if result["success"]:
                # problem_definition의 hard/soft 딕셔너리도 업데이트
                from_cat = "soft" if to_cat == "hard" else "hard"
                from_key = f"{from_cat}_constraints"
                to_key = f"{to_cat}_constraints"

                if cname in state.problem_definition.get(from_key, {}):
                    moved_data = state.problem_definition[from_key].pop(cname)
                    if to_key not in state.problem_definition:
                        state.problem_definition[to_key] = {}
                    state.problem_definition[to_key][cname] = moved_data

                save_session_state(project_id, state)

                name_ko = dk.get_constraint(cname)
                if name_ko and isinstance(name_ko, dict):
                    name_ko = name_ko.get("description", cname)
                else:
                    name_ko = cname

                return {
                    "type": "problem_definition",
                    "text": (
                        f"\u2705 **{name_ko}** \uc81c\uc57d\uc744 **{to_cat.upper()}**\ub85c \ubcc0\uacbd\ud588\uc2b5\ub2c8\ub2e4.\n\n"
                        f"\uc544\ub798 \uc81c\uc548\uc744 \ud655\uc778\ud558\uc2dc\uace0, \ucd94\uac00 \uc218\uc815\uc774 \ud544\uc694\ud558\uba74 \uc624\ub978\ucabd \ud328\ub110\uc5d0\uc11c \uc218\uc815\ud574\uc8fc\uc138\uc694."
                    ),
                    "data": {
                        "view_mode": "problem_definition",
                        "proposal": state.problem_definition,
                        "hard_constraints": state.problem_definition.get("hard_constraints", {}),
                        "soft_constraints": state.problem_definition.get("soft_constraints", {}),
                        "objective": state.problem_definition.get("objective"),
                        "parameters": state.problem_definition.get("parameters", {}),
                        "agent_status": "category_modified",
                    },
                    "options": [
                        {"label": "\ud655\uc778", "action": "send", "message": "\ud655\uc778"},
                        {"label": "\uc218\uc815", "action": "send", "message": "\uc218\uc815"},
                        {"label": "\ub2e4\uc2dc \ubd84\uc11d", "action": "send", "message": "\ub2e4\uc2dc \ubd84\uc11d"},
                    ],
                }

            elif result["needs_confirm"]:
                # 경고 표시, 다시 같은 명령을 보내면 force 적용
                state._pending_category_change = {"constraint": cname, "to": to_cat}
                save_session_state(project_id, state)

                return {
                    "type": "problem_definition",
                    "text": (
                        f"{result['warning']}\n\n"
                        f"변경을 확정하려면 동일한 명령을 다시 입력하세요:\n"
                        f"{cname} {to_cat}로 변경"
                    ),
                    "data": {"agent_status": "category_change_pending"},
                    "options": [
                        {"label": f"{cname} {to_cat}로 변경", "action": "send", "message": f"{cname} {to_cat}로 변경"},
                        {"label": "취소", "action": "send", "message": "취소"},
                    ],
                }

            else:
                return {
                    "type": "problem_definition",
                    "text": f"❌ {result['warning']}",
                    "data": {"agent_status": "category_change_failed"},
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                    ],
                }

        # 파라미터 수정 (key = value 패턴)
        param_pattern = re.compile(r"(\w+)\s*[=:：]\s*(\d+(?:\.\d+)?)")
        matches = param_pattern.findall(message)
        if matches and state.problem_definition:
            params = state.problem_definition.get("parameters", {})
            updated = []
            for key, val in matches:
                val_num = float(val)
                if key in params:
                    params[key]["value"] = val_num
                    params[key]["source"] = "user_modified"
                    updated.append(f"{key} = {val_num}")
                else:
                    params[key] = {"value": val_num, "source": "user_input"}
                    updated.append(f"{key} = {val_num}")

                # 제약조건 values에도 반영
                for category in ["hard_constraints", "soft_constraints"]:
                    for cname, cdata in state.problem_definition.get(category, {}).items():
                        if key in cdata.get("values", {}):
                            cdata["values"][key]["value"] = val_num
                            cdata["values"][key]["source"] = "user_modified"
                            if cdata.get("status") == "user_input_required":
                                cdata["status"] = "user_provided"

            if updated:
                save_session_state(project_id, state)
                return {
                    "type": "problem_definition",
                    "text": (
                        f"파라미터를 수정했습니다: {', '.join(updated)}\n\n"
                        "**확인**을 입력하면 문제 정의가 확정됩니다."
                    ),
                    "data": {
                        "view_mode": "problem_definition",
                        "proposal": state.problem_definition,
                        "agent_status": "parameters_modified",
                    },
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "추가 수정", "action": "send", "message": "수정"},
                    ],
                }

        # 기타: 자유 텍스트 → LLM Smart Apply
        # 사용자가 자연어로 요구사항을 전달하면 LLM이 구조화된 변경사항으로 변환하여 직접 적용
        if model and len(message.strip()) > 5:
            return await self._llm_smart_apply(model, state, project_id, message)

        # LLM 사용 불가 시 fallback
        return {
            "type": "problem_definition",
            "text": (
                "**확인**, **수정**, 또는 **다시 분석**을 입력해주세요.\n"
                "파라미터 수정은 `파라미터명 = 값` 형식으로 입력할 수 있습니다."
            ),
            "data": {
                "view_mode": "problem_definition",
                "proposal": state.problem_definition,
                "agent_status": "awaiting_response",
            },
            "options": [
                {"label": "확인", "action": "send", "message": "확인"},
                {"label": "수정", "action": "send", "message": "수정"},
                {"label": "다시 분석", "action": "send", "message": "다시 분석"},
            ],
        }

    # ──────────────────────────────────────
    # LLM Smart Apply: 자연어 → 구조화된 변경사항 적용
    # ──────────────────────────────────────
    async def _llm_smart_apply(
        self, model, state, project_id: str, message: str
    ) -> Dict:
        """
        사용자의 자연어 요구사항을 LLM이 구조화된 JSON 액션으로 변환하고,
        변환된 액션을 problem_definition state에 직접 적용한다.
        """
        import asyncio
        import json as _json

        current_pd = state.problem_definition or {}
        current_obj = current_pd.get("objective", {})
        hard_constraints = current_pd.get("hard_constraints", {})
        soft_constraints = current_pd.get("soft_constraints", {})
        params = current_pd.get("parameters", {})

        # 사용 가능한 목적함수 목록 로드
        dk = self._load_domain(state)
        domain_name = current_pd.get("domain", "railway")
        objectives_map = {}
        try:
            import yaml as _yaml
            _cpath = f"knowledge/domains/{domain_name}/constraints.yaml"
            with open(_cpath, encoding="utf-8") as _f:
                _cdata = _yaml.safe_load(_f)
            objectives_map = _cdata.get("objectives", {})
        except Exception:
            pass

        obj_list_str = "\n".join([
            f"  - {k}: {v.get('description_ko', v.get('description', k))}"
            for k, v in objectives_map.items()
        ]) if objectives_map else "  (없음)"

        hard_detail = "\n".join([
            f"  - {k}: {v.get('name_ko', k)} (params: {list(v.get('values', {}).keys())})"
            for k, v in hard_constraints.items()
        ]) if hard_constraints else "  (없음)"

        soft_detail = "\n".join([
            f"  - {k}: {v.get('name_ko', k)} (params: {list(v.get('values', {}).keys())})"
            for k, v in soft_constraints.items()
        ]) if soft_constraints else "  (없음)"

        param_detail = "\n".join([
            f"  - {k}: value={v.get('value', 'N/A')}, description={v.get('description', '')}"
            for k, v in params.items()
        ]) if params else "  (없음)"

        smart_prompt = f"""사용자가 최적화 문제 정의를 수정하려고 합니다.

## 사용자 요청
"{message}"

## 현재 문제 정의
목적함수: {current_obj.get('description_ko', current_obj.get('description', 'N/A'))} ({current_obj.get('target', 'N/A')})
Hard 제약조건:
{hard_detail}
Soft 제약조건:
{soft_detail}
파라미터:
{param_detail}

## 사용 가능한 목적함수
{obj_list_str}

## 지시사항
사용자 요청을 분석하여 아래 JSON 형식으로 정확히 응답하세요.
반드시 JSON만 출력하고 다른 텍스트를 포함하지 마세요.

```json
{{
  "actions": [
    {{
      "type": "set_param",
      "param_id": "파라미터ID",
      "value": 값(숫자),
      "description": "설명"
    }},
    {{
      "type": "change_objective",
      "target": "목적함수ID (위 목록에서 선택)",
      "reason": "변경 사유"
    }},
    {{
      "type": "add_constraint",
      "name": "제약조건_영문ID",
      "name_ko": "한국어 이름",
      "category": "hard 또는 soft",
      "description": "제약조건 설명",
      "parameters": {{"param_id": 값}}
    }},
    {{
      "type": "move_constraint",
      "name": "기존 제약조건명",
      "to": "hard 또는 soft"
    }},
    {{
      "type": "remove_constraint",
      "name": "제거할 제약조건명"
    }}
  ],
  "summary": "사용자에게 보여줄 변경 내용 요약 (한국어, markdown)"
}}
```

규칙:
- 파라미터 값을 설정할 때, 기존 파라미터 목록에 없으면 새로 추가합니다.
- 목적함수 변경은 사용 가능한 목록에서 선택합니다.
- 새 제약조건 추가 시 의미 있는 영문 ID를 생성하세요.
- 사용자가 명시하지 않은 것은 변경하지 마세요.
- actions 배열에 변경사항이 없으면 빈 배열을 반환하세요."""

        try:
            response = await asyncio.to_thread(model.generate_content, smart_prompt)
            raw = response.text.strip()

            # JSON 추출
            code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
            if code_match:
                raw = code_match.group(1)
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not brace_match:
                raise ValueError("No JSON in LLM response")
            parsed = _json.loads(brace_match.group(0))
        except Exception as e:
            logger.warning(f"LLM Smart Apply parse failed: {e}")
            # fallback: 기존 분석 모드
            try:
                fallback_prompt = (
                    f"사용자 요청: \"{message}\"\n\n"
                    f"현재 목적함수: {current_obj.get('description', 'N/A')}\n"
                    f"Hard 제약: {list(hard_constraints.keys())}\n"
                    f"Soft 제약: {list(soft_constraints.keys())}\n\n"
                    f"이 요청을 문제 정의에 어떻게 반영해야 할지 한국어로 안내하세요."
                )
                fb_resp = await asyncio.to_thread(model.generate_content, fallback_prompt)
                return {
                    "type": "problem_definition",
                    "text": fb_resp.text.strip(),
                    "data": {
                        "view_mode": "problem_definition",
                        "proposal": state.problem_definition,
                        "agent_status": "llm_guidance",
                    },
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "수정", "action": "send", "message": "수정"},
                    ],
                }
            except Exception:
                return {
                    "type": "problem_definition",
                    "text": "요청을 처리하지 못했습니다. `파라미터명 = 값` 형식으로 입력해 주세요.",
                    "data": {"proposal": state.problem_definition},
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "수정", "action": "send", "message": "수정"},
                    ],
                }

        # ── 액션 적용 ──
        actions = parsed.get("actions", [])
        summary = parsed.get("summary", "")
        applied = []
        pd = state.problem_definition

        for action in actions:
            atype = action.get("type", "")

            if atype == "set_param":
                pid = action.get("param_id", "")
                val = action.get("value")
                desc = action.get("description", "")
                if pid and val is not None:
                    if pid in pd.get("parameters", {}):
                        pd["parameters"][pid]["value"] = val
                        if desc:
                            pd["parameters"][pid]["description"] = desc
                    else:
                        pd.setdefault("parameters", {})[pid] = {
                            "value": val,
                            "description": desc,
                            "status": "user_set",
                        }
                    applied.append(f"파라미터 `{pid}` = {val}")

            elif atype == "change_objective":
                target = action.get("target", "")
                if target and target in objectives_map:
                    odata = objectives_map[target]
                    pd["objective"] = {
                        "type": odata["type"],
                        "target": target,
                        "description": odata["description"],
                        "description_ko": odata.get("description_ko", odata["description"]),
                        "expression": odata.get("expression", ""),
                        "alternatives": [
                            {"target": k, "description": v.get("description_ko", v["description"])}
                            for k, v in objectives_map.items() if k != target
                        ],
                    }
                    applied.append(f"목적함수 → **{odata.get('description_ko', target)}**")

            elif atype == "add_constraint":
                cname = action.get("name", "")
                cat = action.get("category", "hard")
                if cname:
                    ckey = f"{cat}_constraints"
                    pd.setdefault(ckey, {})[cname] = {
                        "name_ko": action.get("name_ko", cname),
                        "description": action.get("description", ""),
                        "status": "confirmed",
                        "values": action.get("parameters", {}),
                    }
                    applied.append(f"제약조건 추가: `{cname}` ({cat})")

            elif atype == "move_constraint":
                cname = action.get("name", "")
                to_cat = action.get("to", "")
                if cname and to_cat:
                    from_cat = "soft" if to_cat == "hard" else "hard"
                    from_key = f"{from_cat}_constraints"
                    to_key = f"{to_cat}_constraints"
                    if cname in pd.get(from_key, {}):
                        moved = pd[from_key].pop(cname)
                        pd.setdefault(to_key, {})[cname] = moved
                        applied.append(f"제약조건 이동: `{cname}` → {to_cat}")

            elif atype == "remove_constraint":
                cname = action.get("name", "")
                if cname:
                    for ckey in ["hard_constraints", "soft_constraints"]:
                        if cname in pd.get(ckey, {}):
                            del pd[ckey][cname]
                            applied.append(f"제약조건 제거: `{cname}`")
                            break

        state.problem_definition = pd
        save_session_state(project_id, state)

        if not applied:
            result_text = summary if summary else "요청을 분석했지만 구체적인 변경 사항이 없습니다."
        else:
            changes_text = "\n".join([f"  - {a}" for a in applied])
            result_text = (
                f"✅ **{len(applied)}건의 변경사항을 적용했습니다:**\n{changes_text}"
            )
            if summary:
                result_text += f"\n\n{summary}"

        return {
            "type": "problem_definition",
            "text": result_text,
            "data": {
                "view_mode": "problem_definition",
                "proposal": state.problem_definition,
                "agent_status": "smart_apply_done",
            },
            "options": [
                {"label": "✅ 확인 (문제 정의 확정)", "action": "send", "message": "확인"},
                {"label": "✏️ 추가 수정", "action": "send", "message": "수정"},
                {"label": "🔄 다시 분석", "action": "send", "message": "다시 분석"},
            ],
        }


# ── 모듈 레벨 함수 ──
_skill_instance: Optional[ProblemDefinitionSkill] = None


def get_skill() -> ProblemDefinitionSkill:
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = ProblemDefinitionSkill()
    return _skill_instance


async def skill_problem_definition(
    model, session: CrewSession, project_id: str,
    message: str, params: Dict
) -> Dict:
    skill = get_skill()
    return await skill.handle(model, session, project_id, message, params)
