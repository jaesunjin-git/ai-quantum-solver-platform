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
            from knowledge.domain_loader import load_domain_knowledge, detect_domain_from_keywords
        except ImportError:
            logger.warning("domain_loader not available, falling back")
            return None

        domain = state.detected_domain or "generic"

        # 직접 이름으로 시도
        dk = load_domain_knowledge(domain)
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

        # crew -> railway 매핑 시도
        alias_map = {"crew": "railway", "train": "railway", "bus": "bus", "flight": "aviation"}
        alias = alias_map.get(domain.lower())
        if alias:
            dk = load_domain_knowledge(alias)
            if dk.hard_constraints or dk.raw_single:
                return dk

        return dk

    # ──────────────────────────────────────
    # public entry point
    # ──────────────────────────────────────
    async def handle(
        self, model, session: CrewSession, project_id: str,
        message: str, params: Dict
    ) -> Dict:
        state = session.state

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

            hard_results[cname] = {
                "name_ko": cdata.get("name_ko", cname),
                "type": ctype,
                "description": cdata.get("description", ""),
                "status": extraction.get("status", "unknown"),
                "values": extraction.get("values", {}),
                "computation_phase": extraction.get("computation_phase"),
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

            soft_results[cname] = {
                "name_ko": cdata.get("name_ko", cname),
                "type": cdata.get("type", "single_param"),
                "description": cdata.get("description", ""),
                "weight": default_weight,
                "weight_range": weight_range,
                "status": "default",
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
        params = cdata.get("parameters", {})
        values = {}
        all_found = True

        for pname, pinfo in params.items():
            better_cdata = self._find_best_cdata_for_param(pname, cdata)
            value = self._search_phase1_params(pname, better_cdata, phase1_data)
            if value is not None:
                values[pname] = {"value": value, "source": "phase1_data", "confidence": 0.8}
            else:
                values[pname] = {"value": None, "source": "user_input_required"}
                all_found = False

        status = "extracted" if all_found else ("partial" if any(v["value"] is not None for v in values.values()) else "user_input_required")
        return {"status": status, "values": values}

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
            context = str(p.get("context", "")).lower()
            value = p.get("value")

            if value is None:
                continue

            # 숫자 변환
            try:
                num_val = float(value)
            except (ValueError, TypeError):
                continue

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
        """제약조건에서 필요한 파라미터를 종합하여 수집"""
        parameters = {}

        # 제약조건의 값에서 파라미터 수집
        for category in ["hard", "soft"]:
            for cname, cinfo in constraints.get(category, {}).items():
                for pname, pval in cinfo.get("values", {}).items():
                    if pname not in parameters:
                        parameters[pname] = pval

        # reference_ranges에서 참고 범위 추가
        if dk:
            sub_domain = self._detect_sub_domain(state, dk)
            if sub_domain:
                for pname in list(parameters.keys()):
                    ref = dk.get_reference_range(sub_domain, pname)
                    if ref and parameters[pname].get("value") is None:
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
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}")
                lines.append("")

            if partial:
                lines.append("**⚠️ 일부 확인된 제약 (Hard):**")
                for cname, cdata in partial.items():
                    name_ko = cdata.get("name_ko", cname)
                    values_str = self._format_values(cdata.get("values", {}))
                    lines.append(f"- **{name_ko}** [{cdata.get('type','')}]: {values_str}")
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
                lines.append(f"- **{name_ko}**: {cdata.get('description','')} (가중치: {weight})")
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
                    "다음 단계: 데이터 정규화 (Phase 2)\n"
                    "데이터를 수학 모델에 맞는 형태로 변환합니다."
                ),
                "data": {
                    "view_mode": "problem_defined",
                    "confirmed_problem": state.confirmed_problem,
                    "agent_status": "problem_defined",
                },
                "options": [
                    {"label": "데이터 정규화 시작", "action": "send", "message": "데이터 정규화 시작"},
                ],
            }

        # 수정 요청
        if any(kw in msg_lower for kw in modify):
            return {
                "type": "problem_definition",
                "text": (
                    "수정할 항목을 알려주세요. 예시:\n\n"
                    "- 목적함수를 [목적함수명]으로 변경\n"
                    "- [파라미터명] = [값]\n"
                    "- [제약조건명] 제거\n"
                ),
                "data": {"agent_status": "modification_pending"},
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
                        "proposal": state.problem_definition,
                        "agent_status": "parameters_modified",
                    },
                    "options": [
                        {"label": "확인", "action": "send", "message": "확인"},
                        {"label": "추가 수정", "action": "send", "message": "수정"},
                    ],
                }

        # 기타
        return {
            "type": "problem_definition",
            "text": (
                "**확인**, **수정**, 또는 **다시 분석**을 입력해주세요.\n"
                "파라미터 수정은 `파라미터명 = 값` 형식으로 입력할 수 있습니다."
            ),
            "data": {"agent_status": "awaiting_response"},
            "options": [
                {"label": "확인", "action": "send", "message": "확인"},
                {"label": "수정", "action": "send", "message": "수정"},
                {"label": "다시 분석", "action": "send", "message": "다시 분석"},
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
