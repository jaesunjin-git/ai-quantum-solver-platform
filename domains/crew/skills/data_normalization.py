from __future__ import annotations
"""
domains/crew/skills/data_normalization.py

Data Normalization Skill.

confirmed_problem + 기존 분석 결과를 바탕으로
LLM에게 매핑 규칙을 1회 요청하고,
confidence 기준으로 자동 확정 / 사용자 확인 분류 후,
확인된 규칙으로 실제 데이터 변환을 실행하여
normalized/ 폴더에 저장한다.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

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


class DataNormalizationSkill:

    def __init__(self):
        self.config = _load_yaml("prompts/data_normalization.yaml")
        self.confidence_threshold = self.config.get("confidence_threshold", 0.8)
        self.data_detection = _load_yaml("knowledge/data_detection.yaml")

    # ──────────────────────────────────────
    # public entry point
    # ──────────────────────────────────────
    async def handle(
        self, model, session: CrewSession, project_id: str,
        message: str, params: Dict
    ) -> Dict:
        state = session.state

        # 매핑 제안 후 사용자 응답 대기 중
        if state.normalization_mapping and not state.normalization_confirmed:
            return await self._handle_user_response(
                model, session, project_id, message
            )

        # 첫 진입: LLM으로 매핑 생성
        mapping_result = await self._generate_mapping(model, state)

        if not mapping_result:
            return {
                "type": "error",
                "text": "데이터 매핑 생성에 실패했습니다. 다시 시도해주세요.",
                "data": None,
                "options": [
                    {"label": "재시도", "action": "send",
                     "message": "데이터 정규화 시작"},
                ],
            }

        # confidence 기준으로 분류
        auto_confirmed = []
        needs_review = []
        for m in mapping_result.get("mappings", []):
            if m.get("confidence", 0) >= self.confidence_threshold:
                auto_confirmed.append(m)
            else:
                needs_review.append(m)

        # 세션에 저장
        state.normalization_mapping = {
            "auto_confirmed": auto_confirmed,
            "needs_review": needs_review,
            "all_mappings": mapping_result.get("mappings", []),
        }
        save_session_state(project_id, state)

        # needs_review가 없으면 바로 변환 실행
        if not needs_review:
            return await self._execute_normalization(
                model, session, project_id
            )

        # 사용자 확인 필요
        response_text = self._format_mapping_result(
            auto_confirmed, needs_review
        )

        return {
            "type": "data_normalization",
            "text": response_text,
            "data": {
                "view_mode": "normalization_mapping",
                "mappings": {
                    "auto_confirmed": auto_confirmed,
                    "needs_review": needs_review,
                },
                "agent_status": "normalization_proposed",
            },
            "options": [
                {"label": "확인", "action": "send", "message": "확인"},
                {"label": "수정", "action": "send", "message": "수정"},
            ],
        }

    # ──────────────────────────────────────
    # LLM 매핑 생성 (1회 호출)
    # ──────────────────────────────────────
    async def _generate_mapping(self, model, state) -> Optional[dict]:
        confirmed = state.confirmed_problem or {}
        stage = confirmed.get("stage", "task_generation")

        # 필요한 테이블 목록
        required = self.config.get("required_tables", {}).get(stage, {})

        # 프롬프트 조립
        system = self.config.get("system_prompt", "")
        rules = self.config.get("rules", [])
        rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))
        output_schema = self.config.get("output_schema", "{}")

        # confirmed_problem 요약
        problem_summary = json.dumps(confirmed, ensure_ascii=False, indent=2)

        # 데이터 구조 요약
        data_summary = state.csv_summary or "데이터 요약 없음"
        if len(data_summary) > 4000:
            data_summary = data_summary[:4000]

        # Build accurate file inventory from upload directory
        file_inventory = ''
        try:
            import pathlib as _pl
            _upload_dir = _pl.Path('uploads') / str(state.project_id)
            if _upload_dir.exists():
                _inv_lines = []
                for _fp in sorted(_upload_dir.iterdir()):
                    if _fp.is_dir():
                        continue
                    _ext = _fp.suffix.lower()
                    if _ext in ('.xlsx', '.xls'):
                        try:
                            import openpyxl as _opx
                            _wb = _opx.load_workbook(str(_fp), read_only=True)
                            for _sh in _wb.sheetnames:
                                _inv_lines.append(f'  - source_file: {_fp.name}  source_sheet: {_sh}')
                            _wb.close()
                        except Exception:
                            _inv_lines.append(f'  - source_file: {_fp.name}')
                    elif _ext == '.csv':
                        _inv_lines.append(f'  - source_file: {_fp.name}  source_sheet: null')
                    elif _ext in ('.txt', '.md', '.text'):
                        _inv_lines.append(f'  - source_file: {_fp.name}  source_sheet: null')
                file_inventory = chr(10).join(_inv_lines)
        except Exception as _inv_e:
            logger.warning(f'File inventory build failed: {_inv_e}')


        # data_profile 요약
        profile_summary = ""
        if getattr(state, 'data_profile', None) and isinstance(getattr(state, 'data_profile', None), dict):
            for sheet_key, info in getattr(state, 'data_profile', None).get("files", {}).items():
                cols = info.get("columns", {})
                col_names = list(cols.keys())[:20]
                structure = info.get("structure", "tabular")
                profile_summary += (
                    f"\n[{sheet_key}] {info.get('rows', 0)} rows, "
                    f"structure={structure}, "
                    f"columns={col_names}"
                )

        # 필요한 테이블 설명
        tables_desc = ""
        for table_name, table_info in required.items():
            cols = table_info.get("columns", [])
            desc = table_info.get("description", "")
            req = "필수" if table_info.get("required", True) else "선택"
            tables_desc += (
                f"\n  - {table_name} ({req}): {desc}"
                f"\n    columns: {cols}"
            )

        prompt = f"""{system}

Rules:
{rules_text}

Output JSON Schema:
{output_schema}

[Confirmed Problem Definition]
{problem_summary}

[Required Normalized Tables]
{tables_desc}

[Available Source Files - USE THESE EXACT NAMES]
{file_inventory}
IMPORTANT: source_file MUST be an exact filename from this list.
Sheet names are NOT file names. For example, use source_file="\ub370\uc774\ud130\uc14b.xlsx" with source_sheet="\uae30\uad00\uc0ac \uadfc\ub85c\uc2dc\uac04 \uad6c\uc131", NOT source_file="\uae30\uad00\uc0ac \uadfc\ub85c\uc2dc\uac04 \uad6c\uc131.xlsx".

[Data Structure Summary]
{data_summary}

[Data Profile]
{profile_summary}

Generate the mapping JSON now."""

        try:
            response = await asyncio.to_thread(
                model.generate_content, prompt
            )
            text = response.text.strip()

            # Remove markdown code fences
            if text.startswith('```'):
                text = text.split(chr(10), 1)[-1]
            if text.rstrip().endswith('```'):
                text = text.rstrip().rsplit('```', 1)[0]
            text = text.strip()

            # Extract JSON object
            json_match = re.search(r'\{[\s\S]*\}', text)
            raw_json = json_match.group() if json_match else text

            # Fix invalid backslash escapes in JSON
            cleaned = []
            i = 0
            while i < len(raw_json):
                if raw_json[i] == chr(92) and i + 1 < len(raw_json):
                    nxt = raw_json[i + 1]
                    if nxt in '"/\\bfnrtu':
                        cleaned.append(raw_json[i])
                        cleaned.append(nxt)
                        i += 2
                        continue
                    else:
                        cleaned.append(chr(92))
                        cleaned.append(chr(92))
                        i += 1
                        continue
                cleaned.append(raw_json[i])
                i += 1
            raw_json = ''.join(cleaned)

            mapping_result = json.loads(raw_json)

            # Post-process: ensure pivot timetables are mapped as trips
            try:
                import pathlib as _pl
                _upload_dir = _pl.Path('uploads') / str(state.project_id)
                _existing_trip_files = set()
                for _m in mapping_result.get('mappings', []):
                    if _m.get('target_table') == 'trips':
                        _existing_trip_files.add(_m.get('source_file', ''))
                # Scan for pivot timetable files not already mapped as trips
                if _upload_dir.exists():
                    for _fp in _upload_dir.iterdir():
                        if not _fp.is_file() or _fp.suffix.lower() not in ('.xlsx', '.xls', '.csv'):
                            continue
                        if _fp.name in _existing_trip_files:
                            continue
                        # Check structure: .1 suffix columns + time data = pivot timetable
                        try:
                            import pandas as _pd
                            if _fp.suffix.lower() == '.csv':
                                _tdf = _pd.read_csv(str(_fp), nrows=3)
                            else:
                                _tdf = _pd.read_excel(str(_fp), nrows=3)
                            _cols = [str(c) for c in _tdf.columns]
                            _suffix_count = sum(1 for c in _cols if '.1' in c)
                            _time_count = 0
                            for _c in _tdf.columns:
                                _s = _tdf[_c].dropna().head(3)
                                _tc = sum(1 for _v in _s if self._to_minutes(_v) is not None)
                                if len(_s) > 0 and _tc / len(_s) >= 0.7:
                                    _time_count += 1
                            if _suffix_count >= 3 and _time_count >= 10:
                                logger.info(f'Auto-add trips mapping: {_fp.name} (suffix={_suffix_count}, time_cols={_time_count})')
                                mapping_result.setdefault('mappings', []).append({
                                    'target_table': 'trips',
                                    'source_file': _fp.name,
                                    'source_sheet': 'Sheet1',
                                    'transform_type': 'unpivot',
                                    'confidence': 0.95,
                                    'reason': 'Auto-detected pivot timetable structure',
                                    'column_mapping': {}
                                })
                        except Exception as _inner_e:
                            logger.warning(f"Auto-add inner error for {_fp.name}: {_inner_e}")
            except Exception as _pp_e:
                logger.warning(f"Mapping post-process failed: {_pp_e}", exc_info=True)


            # Post-process 2: auto-add unmapped small Excel sheets as parameters
            try:
                _mapped_sources = set()
                for _m in mapping_result.get('mappings', []):
                    _key = f"{_m.get('source_file', '')}:{_m.get('source_sheet', '')}"
                    _mapped_sources.add(_key)
                if _upload_dir.exists():
                    for _fp in _upload_dir.iterdir():
                        if not _fp.is_file() or _fp.suffix.lower() not in ('.xlsx', '.xls'):
                            continue
                        try:
                            import openpyxl as _opx
                            _wb = _opx.load_workbook(str(_fp), read_only=True)
                            for _sh in _wb.sheetnames:
                                _key = f"{_fp.name}:{_sh}"
                                if _key in _mapped_sources:
                                    continue
                                # Read sheet to check size
                                import pandas as _pd2
                                _sdf = _pd2.read_excel(str(_fp), sheet_name=_sh)
                                if len(_sdf) <= 30:
                                    logger.info(f'Auto-add parameters: {_key} ({len(_sdf)} rows, unmapped small table)')
                                    mapping_result.setdefault('mappings', []).append({
                                        'target_table': 'parameters',
                                        'source_file': _fp.name,
                                        'source_sheet': _sh,
                                        'transform_type': 'extract_kv',
                                        'confidence': 0.6,
                                        'reason': f'Auto-detected unmapped small table ({len(_sdf)} rows)',
                                        'column_mapping': {}
                                    })
                            _wb.close()
                        except Exception as _sh_e:
                            logger.warning(f'Auto-add sheet scan error for {_fp.name}: {_sh_e}')
            except Exception as _pp2_e:
                logger.warning(f'Auto-add unmapped sheets failed: {_pp2_e}')

            # Post-process 3: ensure confirmed_problem parameters are always included
            _has_confirmed = any(
                _m.get('transform_type') == 'from_confirmed'
                for _m in mapping_result.get('mappings', [])
            )
            if not _has_confirmed:
                _cp = getattr(state, 'confirmed_problem', None) or {}
                if _cp.get('parameters'):
                    logger.info('Auto-add: confirmed_problem parameters')
                    mapping_result.setdefault('mappings', []).append({
                        'target_table': 'parameters',
                        'source_file': '',
                        'source_sheet': '',
                        'transform_type': 'from_confirmed',
                        'confidence': 1.0,
                        'reason': 'Default parameters from confirmed problem definition',
                        'column_mapping': {}
                    })

            return mapping_result

        except json.JSONDecodeError as e:
            logger.error(f"Mapping JSON parse error: {e}")
            logger.error(f"Raw response: {text[:500]}")
            return None
        except Exception as e:
            logger.error(f"Mapping generation failed: {e}", exc_info=True)
            return None

    # ──────────────────────────────────────
    # 사용자 응답 처리
    # ──────────────────────────────────────
    async def _handle_user_response(
        self, model, session: CrewSession, project_id: str, message: str
    ) -> Dict:
        state = session.state
        keywords = self.config.get("confirmation_keywords", {})
        msg_lower = message.strip().lower()

        positive = [k.lower() for k in keywords.get("positive", [])]
        modify = [k.lower() for k in keywords.get("modify", [])]
        restart = [k.lower() for k in keywords.get("restart", [])]

        # 확인 → 변환 실행
        if any(kw in msg_lower for kw in positive):
            return await self._execute_normalization(
                model, session, project_id
            )

        # 수정 요청
        if any(kw in msg_lower for kw in modify):
            return {
                "type": "data_normalization",
                "text": (
                    "수정할 매핑을 알려주세요. 예시:\n\n"
                    "- trips의 source를 다른 시트로 변경\n"
                    "- dep_station 컬럼을 출발역으로 매핑\n"
                ),
                "data": {"agent_status": "modification_pending"},
                "options": [],
            }

        # 재시작
        if any(kw in msg_lower for kw in restart):
            state.normalization_mapping = None
            state.normalization_confirmed = False
            state.data_normalized = False
            save_session_state(project_id, state)
            return {
                "type": "info",
                "text": "데이터 정규화를 초기화했습니다.",
                "data": {"agent_status": "normalization_reset"},
                "options": [
                    {"label": "정규화 재시작", "action": "send",
                     "message": "데이터 정규화 시작"},
                ],
            }

        # 기타: 사용자의 수정 요청을 LLM에 전달하여 매핑 업데이트
        try:
            current_mapping = state.normalization_mapping or {}
            current_json = json.dumps(current_mapping, ensure_ascii=False, indent=2)

            modify_prompt = f"""You previously generated a data normalization mapping.
Current mapping:
{current_json}

The user wants to modify it:
\"{message}\"

Apply the user's modification and return the COMPLETE updated mapping as JSON.
Keep all existing mappings and only change what the user requested.
Return format: {{"mappings": [...]}}
Each mapping must have: target_table, source_file, source_sheet, transform_type, confidence, reason, column_mapping
"""
            response = await asyncio.to_thread(
                model.generate_content, modify_prompt
            )
            text = response.text.strip()

            # Clean JSON
            if text.startswith('```'):
                text = text.split(chr(10), 1)[-1]
            if text.rstrip().endswith('```'):
                text = text.rstrip().rsplit('```', 1)[0]
            text = text.strip()

            json_match = re.search(r'\{[\s\S]*\}', text)
            raw = json_match.group() if json_match else text
            updated = json.loads(raw)

            # Re-classify by confidence
            auto_confirmed = []
            needs_review = []
            for m in updated.get('mappings', []):
                if m.get('confidence', 0) >= self.confidence_threshold:
                    auto_confirmed.append(m)
                else:
                    needs_review.append(m)

            state.normalization_mapping = {
                'auto_confirmed': auto_confirmed,
                'needs_review': needs_review,
                'all_mappings': updated.get('mappings', []),
            }
            save_session_state(project_id, state)

            response_text = self._format_mapping_result(auto_confirmed, needs_review)
            return {
                'type': 'data_normalization',
                'text': '매핑이 수정되었습니다.\n\n' + response_text,
                'data': {
                    'view_mode': 'normalization_mapping',
                    'mappings': {
                        'auto_confirmed': auto_confirmed,
                        'needs_review': needs_review,
                    },
                    'agent_status': 'normalization_proposed',
                },
                'options': [
                    {'label': '확인', 'action': 'send', 'message': '확인'},
                    {'label': '수정', 'action': 'send', 'message': '수정'},
                ],
            }

        except Exception as e:
            logger.error(f'Mapping modification failed: {e}', exc_info=True)
            return {
                'type': 'data_normalization',
                'text': f'매핑 수정에 실패했습니다: {e}\n\n**확인**, **수정**, 또는 **다시**를 입력해주세요.',
                'data': {'agent_status': 'awaiting_response'},
                'options': [
                    {'label': '확인', 'action': 'send', 'message': '확인'},
                    {'label': '수정', 'action': 'send', 'message': '수정'},
                ],
            }

    # ──────────────────────────────────────
    # 변환 실행
    # ──────────────────────────────────────
    async def _execute_normalization(
        self, model, session: CrewSession, project_id: str
    ) -> Dict:
        state = session.state
        mapping = state.normalization_mapping
        if not mapping:
            return {
                "type": "error",
                "text": "매핑 정보가 없습니다.",
                "data": None,
                "options": [],
            }

        all_mappings = mapping.get("all_mappings", [])
        upload_dir = _UPLOAD_BASE / re.sub(r"[^a-zA-Z0-9_\-]", "", str(project_id))
        norm_dir = upload_dir / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)

        results = []
        errors = []

        # Collect trip source files to skip them in parameters
        trip_source_files = set()
        for m in all_mappings:
            if m.get("target_table") == "trips":
                trip_source_files.add(m.get("source_file", ""))

        # Track what we've already processed to avoid duplicates
        processed_trips = set()
        processed_params_sources = set()

        for m in all_mappings:
            target = m.get("target_table", "")
            transform = m.get("transform_type", "")
            source_file = m.get("source_file", "")
            source_sheet = m.get("source_sheet", "")

            try:
                # ═══ TRIPS ═══
                if target == "trips":
                    src_key = f"{source_file}:{source_sheet}"
                    if src_key in processed_trips:
                        continue
                    processed_trips.add(src_key)

                    _fp = upload_dir / source_file
                    if not _fp.exists():
                        logger.warning(f"Trip source not found: {_fp}")
                        continue

                    # Structure-based decision: detect pivot timetable
                    is_pivot = False
                    try:
                        _ext = _fp.suffix.lower()
                        if _ext in (".xlsx", ".xls"):
                            _tdf = pd.read_excel(str(_fp), sheet_name=source_sheet or 0, nrows=5)
                        elif _ext == ".csv":
                            _tdf = pd.read_csv(str(_fp), nrows=5)
                        else:
                            _tdf = None
                        if _tdf is not None:
                            _cols = [str(c) for c in _tdf.columns]
                            _has_suffix = sum(1 for c in _cols if ".1" in c) >= 3
                            _time_col_count = 0
                            for _c in _tdf.columns:
                                _sample = _tdf[_c].dropna().head(3)
                                _tc = sum(1 for _v in _sample if self._to_minutes(_v) is not None)
                                if len(_sample) > 0 and _tc / len(_sample) >= 0.7:
                                    _time_col_count += 1
                            if _has_suffix and _time_col_count >= 10:
                                is_pivot = True
                                logger.info(f"Pivot detected: {source_file} (suffix={_has_suffix}, time_cols={_time_col_count})")
                    except Exception as _det_e:
                        logger.warning(f"Pivot detection failed for {source_file}: {_det_e}")

                    if is_pivot:
                        # Pivot timetable -> unpivot
                        df = await self._transform_unpivot_timetable(
                            upload_dir, source_file, source_sheet, m
                        )
                    elif hasattr(self, '_transform_parse_blocks'):
                        # Non-pivot -> try parse_blocks (for DIA-like data)
                        df = await self._transform_parse_blocks(
                            upload_dir, source_file, source_sheet, m
                        )
                    else:
                        df = await self._transform_direct(
                            upload_dir, source_file, source_sheet, m
                        )

                    if df is not None and len(df) > 0:
                        out_path = norm_dir / "trips.csv"
                        # Merge with existing trips
                        if out_path.exists():
                            existing_trips = pd.read_csv(str(out_path), encoding="utf-8")
                            df = pd.concat([existing_trips, df], ignore_index=True)
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"trips.csv: {len(df)} rows")
                    else:
                        errors.append(f"trips from {source_file}: empty result")

                # ═══ PARAMETERS ═══
                elif target == "parameters":
                    # Skip if source file is already mapped as trips
                    if source_file in trip_source_files and source_file != "":
                        logger.info(f"Skipping parameters for {source_file} (already mapped as trips)")
                        continue

                    src_key = f"{source_file}:{source_sheet}"
                    if src_key in processed_params_sources:
                        continue
                    processed_params_sources.add(src_key)

                    df = await self._transform_parameters(
                        state, upload_dir, source_file, source_sheet, m
                    )
                    if df is not None and len(df) > 0:
                        out_path = norm_dir / "parameters.csv"
                        if out_path.exists():
                            existing = pd.read_csv(str(out_path), encoding="utf-8")
                            if "param_name" in df.columns and "param_name" in existing.columns:
                                existing = existing[~existing["param_name"].isin(df["param_name"])]
                            df = pd.concat([existing, df], ignore_index=True)
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"parameters.csv: {len(df)} rows")

                # ═══ EXISTING DUTIES ═══
                elif target == "existing_duties":
                    df = await self._transform_parse_blocks(
                        upload_dir, source_file, source_sheet, m
                    )
                    if df is not None and len(df) > 0:
                        out_path = norm_dir / "existing_duties.csv"
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"existing_duties.csv: {len(df)} rows")

            except Exception as e:
                logger.error(f"Transform error [{target}] {source_file}: {e}", exc_info=True)
                errors.append(f"{target}: {str(e)}")

        # ═══ POST: Ensure confirmed_problem defaults are always in parameters ═══
        try:
            confirmed = getattr(state, "confirmed_problem", None) or {}
            params = confirmed.get("parameters", {})
            if params:
                default_rows = []
                for pname, pinfo in params.items():
                    value = pinfo.get("value") if isinstance(pinfo, dict) else pinfo
                    src = pinfo.get("source", "default") if isinstance(pinfo, dict) else "default"
                    default_rows.append({"param_name": pname, "value": value, "unit": "minutes", "source": src})
                if default_rows:
                    default_df = pd.DataFrame(default_rows)
                    out_path = norm_dir / "parameters.csv"
                    if out_path.exists():
                        existing = pd.read_csv(str(out_path), encoding="utf-8")
                        existing = existing[~existing["param_name"].isin(default_df["param_name"])]
                        default_df = pd.concat([existing, default_df], ignore_index=True)
                    default_df.to_csv(str(out_path), index=False, encoding="utf-8")
                    logger.info(f"Added {len(default_rows)} confirmed_problem defaults to parameters")
        except Exception as _cp_e:
            logger.warning(f"Confirmed problem defaults failed: {_cp_e}")

        # 결과 판단
        if results:
            state.normalization_confirmed = True
            state.data_normalized = True
            state.normalized_data_summary = {
                "files": results,
                "errors": errors,
                "output_dir": str(norm_dir),
            }
            save_session_state(project_id, state)

            result_text = "**데이터 정규화가 완료되었습니다.**\n\n"
            result_text += "생성된 파일:\n"
            for r in results:
                result_text += f"- {r}\n"
            if errors:
                result_text += "\n경고:\n"
                for e in errors:
                    result_text += f"- {e}\n"
            result_text += "\n다음 단계: 수학 모델 생성"

            return {
                "type": "data_normalization",
                "text": result_text,
                "data": {
                    "view_mode": "normalization_complete",
                    "results": results,
                    "errors": errors,
                    "agent_status": "normalization_complete",
                },
                "options": [
                    {"label": "수학 모델 생성", "action": "send",
                     "message": "수학 모델 생성해줘"},
                ],
            }
        else:
            return {
                "type": "error",
                "text": (
                    "데이터 변환에 실패했습니다.\n\n"
                    + "\n".join(f"- {e}" for e in errors)
                ),
                "data": {"agent_status": "normalization_failed"},
                "options": [
                    {"label": "재시도", "action": "send",
                     "message": "데이터 정규화 시작"},
                ],
            }

    # ──────────────────────────────────────
    # 변환 함수들
    # ──────────────────────────────────────
    async def _transform_unpivot_timetable(
        self, upload_dir, source_file: str,
        source_sheet: str, mapping: dict
    ) -> "Optional[pd.DataFrame]":
        """
        Language-agnostic pivot timetable -> row-based trips.
        Uses data patterns to distinguish station columns from meta columns:
        - Station columns: values increase across rows (progressive departure times)
        - Meta columns: values are small durations or non-progressive
        """
        import pandas as pd
        from pathlib import Path

        file_path = Path(upload_dir) / source_file
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return None

        ext = file_path.suffix.lower()
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)
        elif ext == ".csv":
            df = pd.read_csv(str(file_path))
        else:
            return None

        if df.empty:
            return None

        # --- Step 1: Classify columns by time ratio ---
        time_cols = []
        non_time_cols = []

        for col in df.columns:
            sample = df[col].dropna().head(30)
            if len(sample) == 0:
                non_time_cols.append(col)
                continue
            tc = sum(1 for v in sample if self._to_minutes(v) is not None)
            if tc / len(sample) >= 0.7:
                time_cols.append(col)
            else:
                non_time_cols.append(col)

        # --- Step 2: Separate station columns from meta-time columns ---
        # Station columns have large, progressive values (e.g. 316, 317, 319...)
        # Meta columns have small values (e.g. 0, 7, 34) or non-progressive patterns
        station_cols = []
        meta_time_cols = []

        for col in time_cols:
            sample = df[col].dropna().head(20)
            vals = [self._to_minutes(v) for v in sample]
            valid = [m for m in vals if m is not None]

            if len(valid) < 3:
                meta_time_cols.append(col)
                continue

            mean_val = sum(valid) / len(valid)
            spread = max(valid) - min(valid)

            # Count non-time string values (e.g. "inbound" markers)
            non_null_all = df[col].dropna()
            non_time_str_count = sum(
                1 for v in non_null_all
                if pd.notna(v) and self._to_minutes(v) is None
            )

            # Station: mean > 60 AND spread > 120 AND no non-time strings
            # Meta: small values OR has non-time strings mixed in
            if mean_val > 60 and spread > 120 and non_time_str_count == 0:
                station_cols.append(col)
            else:
                meta_time_cols.append(col)

        # --- Step 3: Split forward / reverse ---
        fwd_stations = [c for c in station_cols if ".1" not in str(c)]
        rev_stations = [c for c in station_cols if ".1" in str(c)]

        if not rev_stations and len(station_cols) > 10:
            seen = {}
            fwd_stations = []
            rev_stations = []
            for col in station_cols:
                base = str(col).split(".")[0].strip()
                if base in seen:
                    rev_stations.append(col)
                else:
                    seen[base] = col
                    fwd_stations.append(col)

        has_round_trip = len(rev_stations) >= 3

        logger.info(f"Stations: {len(fwd_stations)} fwd, {len(rev_stations)} rev | "
                     f"Meta-time: {len(meta_time_cols)} | Non-time: {len(non_time_cols)} | "
                     f"Round-trip: {has_round_trip}")

        # --- Step 4: Find ID columns ---
        id_col = None
        id_col_rev = None
        for col in non_time_cols:
            nn = df[col].dropna().head(10)
            if len(nn) == 0:
                continue
            try:
                vals = pd.to_numeric(nn, errors="coerce")
                if vals.notna().sum() >= 8 and (vals % 1 == 0).all():
                    if id_col is None:
                        id_col = col
                    elif id_col_rev is None and str(col) != str(id_col):
                        id_col_rev = col
                        break
            except Exception:
                continue

        logger.info(f"ID columns: fwd={id_col}, rev={id_col_rev}")

        # --- Step 5: Extract trips (standard_columns 참조) ---
        _std = self.data_detection.get("data_types", {}).get("timetable", {}).get("standard_columns", {})
        _col_dep = next((k for k, v in _std.items() if "출발" in str(v) and "시각" in str(v)), "trip_dep_time")
        _col_arr = next((k for k, v in _std.items() if "도착" in str(v) and "시각" in str(v)), "trip_arr_time")
        _col_dur = next((k for k, v in _std.items() if "소요" in str(v)), "trip_duration")
        trips = []

        for idx, row in df.iterrows():
            # Forward
            if fwd_stations:
                tid = row.get(id_col) if id_col else idx * 2 + 1
                if pd.notna(tid):
                    times = []
                    for st in fwd_stations:
                        val = row.get(st)
                        if pd.notna(val):
                            m = self._to_minutes(val)
                            if m is not None:
                                times.append((str(st), m))
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid) if not isinstance(tid, str) else tid,
                            "direction": "forward",
                            "dep_station": times[0][0],
                            "arr_station": times[-1][0],
                            _col_dep: times[0][1],
                            _col_arr: times[-1][1],
                            _col_dur: times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })

            # Reverse
            if has_round_trip and rev_stations:
                tid_r = row.get(id_col_rev) if id_col_rev else (
                    int(row.get(id_col, 0)) + 1 if id_col and pd.notna(row.get(id_col)) else idx * 2 + 2
                )
                if pd.notna(tid_r):
                    times = []
                    for st in rev_stations:
                        val = row.get(st)
                        if pd.notna(val):
                            m = self._to_minutes(val)
                            if m is not None:
                                times.append((str(st).replace(".1", "").strip(), m))
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid_r) if not isinstance(tid_r, str) else tid_r,
                            "direction": "reverse",
                            "dep_station": times[0][0],
                            "arr_station": times[-1][0],
                            _col_dep: times[0][1],
                            _col_arr: times[-1][1],
                            _col_dur: times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })

        if not trips:
            logger.error("No trips extracted")
            return None

        result = pd.DataFrame(trips)
        result = result.sort_values(_col_dep).reset_index(drop=True)
        fwd_c = len(result[result.direction == "forward"])
        rev_c = len(result[result.direction == "reverse"])
        logger.info(f"Extracted {len(result)} trips (forward={fwd_c}, reverse={rev_c})")
        return result

    async def _transform_direct(
        self, upload_dir: Path, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """이미 정규형인 데이터를 컬럼 매핑만 적용"""
        file_path = upload_dir / source_file
        if not file_path.exists():
            return None

        ext = file_path.suffix.lower()
        if ext == ".csv":
            try:
                df = pd.read_csv(str(file_path), encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(str(file_path), encoding="cp949")
        else:
            df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)

        col_mapping = mapping.get("column_mapping", {})
        if col_mapping:
            reverse_map = {v: k for k, v in col_mapping.items() if isinstance(v, str)}
            df = df.rename(columns=reverse_map)

        return df

    def _cell_to_minutes(self, val):
        """Convert a cell value to minutes. Structure-based, no language dependency."""
        import re as _re
        import datetime
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, datetime.time):
            return val.hour * 60 + val.minute
        if isinstance(val, datetime.datetime):
            return val.hour * 60 + val.minute
        s = str(val).strip()
        m = _re.match(r'(\d{1,2}):(\d{2})(?::(\d{2}))?$', s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return h * 60 + mi
        m = _re.match(r'(\d+)\s*(?:\uc2dc\uac04|hours?|hrs?)\s*(?:(\d+)\s*(?:\ubd84|minutes?|mins?))?', s, _re.IGNORECASE)
        if m:
            return int(m.group(1)) * 60 + (int(m.group(2)) if m.group(2) else 0)
        m = _re.match(r'(\d+)\s*(?:\ubd84|minutes?|mins?)$', s, _re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None
    async def _transform_parameters(
        self, state, upload_dir: Path, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """Extract parameters - strategy selected by data structure, not transform_type."""
        rows = []
        transform = mapping.get('transform_type', '')

        if not source_file or transform == 'from_confirmed':
            confirmed = getattr(state, 'confirmed_problem', None) or {}
            params = confirmed.get('parameters', {})
            for pname, pinfo in params.items():
                value = pinfo.get('value') if isinstance(pinfo, dict) else pinfo
                src = pinfo.get('source', 'confirmed') if isinstance(pinfo, dict) else 'confirmed'
                rows.append({'param_name': pname, 'value': value, 'unit': 'minutes', 'source': src})
            return pd.DataFrame(rows) if rows else None

        file_path = upload_dir / source_file
        if not file_path.exists():
            logger.warning(f'Parameter file not found: {file_path}')
            return None

        try:
            ext = file_path.suffix.lower()

            if ext in ('.txt', '.md', '.text'):
                rows.extend(self._extract_params_from_text(file_path))

            elif ext in ('.xlsx', '.xls', '.csv'):
                if ext == '.csv':
                    df = pd.read_csv(str(file_path))
                else:
                    df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)

                n_rows, n_cols = df.shape
                if n_rows <= 30:
                    rows.extend(self._extract_params_from_table(df, source_file, source_sheet))
                else:
                    rows.extend(self._extract_params_from_large_table(df, source_file))

        except Exception as e:
            logger.error(f'Parameter extraction error from {source_file}: {e}', exc_info=True)

        return pd.DataFrame(rows) if rows else None

    def _extract_params_from_text(self, file_path: Path) -> list:
        """Extract parameters from text file using numeric patterns.
        Language-agnostic: looks for number + unit patterns."""
        import re as _re
        rows = []

        text = None
        for enc in ('utf-8', 'cp949', 'euc-kr', 'latin-1'):
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    text = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if not text:
            return rows

        text_lines = text.strip().split('\n')
        param_idx = 0

        for line in text_lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            extracted = []

            for m in _re.finditer(r'(\d+)\s*(?:\uc2dc\uac04|hours?|hrs?)\s*(?:(\d+)\s*(?:\ubd84|minutes?|mins?))?', line, _re.IGNORECASE):
                hours = int(m.group(1))
                mins = int(m.group(2)) if m.group(2) else 0
                total_min = hours * 60 + mins
                extracted.append(('duration', total_min, 'minutes', m.start(), m.end()))

            for m in _re.finditer(r'(\d+)\s*(?:\ubd84|minutes?|mins?)(?!\w)', line, _re.IGNORECASE):
                val = int(m.group(1))
                pos = m.start()
                already = any(pos >= e[3] and pos <= e[4] for e in extracted)
                if not already:
                    extracted.append(('duration', val, 'minutes', pos, m.end()))

            for m in _re.finditer(r'(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)', line):
                h, mi = int(m.group(1)), int(m.group(2))
                if 0 <= h <= 23 and 0 <= mi <= 59:
                    total_min = h * 60 + mi
                    extracted.append(('time_of_day', total_min, 'minutes_from_midnight', m.start(), m.end()))

            for m in _re.finditer(r'(\d+)\s*(?:\uac1c|ea|units?|trips?|duties?|services?)(?:\s|$|[^\w])', line, _re.IGNORECASE):
                extracted.append(('count', int(m.group(1)), 'count', m.start(), m.end()))

            for param_type, value, unit, start_pos, end_pos in extracted:
                param_name = f'{param_type}_{param_idx}'
                context = line.strip()[:120]
                rows.append({'param_name': param_name, 'value': value, 'unit': unit, 'source': f'text:{file_path.name}', 'context': context, 'param_type': param_type})
                param_idx += 1

        logger.info(f'Extracted {len(rows)} parameters from text file {file_path.name}')
        return rows

    def _extract_params_from_table(self, df: pd.DataFrame, source_file: str, source_sheet: str) -> list:
        """Extract parameters from a small table.
        Structure-based: detects key/value columns by dtype distribution."""
        import re as _re
        rows = []

        if df.empty or len(df.columns) < 2:
            return rows

        # Crosstab detection: few rows + many columns = each cell is a parameter
        n_rows, n_cols = df.shape
        if n_rows <= 3 and n_cols >= 3:
            # Treat as crosstab: col_name + row_identifier = cell_value
            # First, identify which columns are identifiers (text) vs values
            id_cols = []
            data_cols = []
            for col in df.columns:
                sample = df[col].dropna()
                if len(sample) == 0:
                    continue
                has_number = False
                for v in sample:
                    s = str(v).strip()
                    if _re.search(r'\d', s):
                        has_number = True
                        break
                if has_number:
                    data_cols.append(col)
                else:
                    id_cols.append(col)
            if not id_cols:
                id_cols = [df.columns[0]]
                data_cols = list(df.columns[1:])
            if data_cols:
                for idx, row in df.iterrows():
                    row_id = '_'.join(str(row.get(c, '')).strip() for c in id_cols if pd.notna(row.get(c)))
                    row_id = _re.sub(r'[\s/()\.]+', '_', row_id).strip('_')
                    for dc in data_cols:
                        raw_val = row.get(dc)
                        if pd.isna(raw_val):
                            continue
                        raw_str = str(raw_val).strip()
                        value = None
                        unit = 'unknown'
                        minutes = self._cell_to_minutes(raw_val)
                        if minutes is not None and minutes > 0:
                            value = minutes
                            unit = 'minutes'
                        if value is None:
                            m = _re.match(r'^([\d.]+)$', raw_str)
                            if m:
                                value = float(m.group(1))
                                unit = 'numeric'
                        if value is None:
                            m = _re.search(r'(\d+)', raw_str)
                            if m:
                                value = int(m.group(1))
                                unit = 'extracted_numeric'
                        if value is not None:
                            col_clean = _re.sub(r'[\s/()\.]+', '_', str(dc)).strip('_')
                            pname = f'{col_clean}_{row_id}' if row_id else col_clean
                            rows.append({'param_name': pname, 'value': value, 'unit': unit, 'source': f'{source_file}:{source_sheet}'})
                logger.info(f'Crosstab detected: {n_rows}x{n_cols}, extracted {len(rows)} params from {source_file}:{source_sheet}')
                return rows


        key_cols = []
        val_cols = []

        for col in df.columns:
            sample = df[col].dropna()
            if len(sample) == 0:
                continue
            numeric_count = 0
            for v in sample:
                s = str(v).strip()
                if _re.search(r'\d+\s*[^\w]*$|\d+:\d+|\d+\.\d+', s):
                    numeric_count += 1
            if len(sample) > 0 and numeric_count / len(sample) > 0.5:
                val_cols.append(col)
            else:
                key_cols.append(col)

        if not key_cols or not val_cols:
            key_cols = list(df.columns[:-1])
            val_cols = [df.columns[-1]]

        for idx, row in df.iterrows():
            name_parts = []
            for kc in key_cols:
                v = row.get(kc)
                if pd.notna(v):
                    name_parts.append(str(v).strip())
            if not name_parts:
                continue

            param_name = '_'.join(name_parts)
            param_name = _re.sub(r'[\s/()\-]+', '_', param_name)
            param_name = _re.sub(r'_+', '_', param_name).strip('_')

            for vc in val_cols:
                raw_val = row.get(vc)
                if pd.isna(raw_val):
                    continue
                raw_str = str(raw_val).strip()
                value = None
                unit = 'unknown'

                minutes = self._cell_to_minutes(raw_val)
                if minutes is not None and minutes > 0:
                    value = minutes
                    unit = 'minutes'

                if value is None:
                    m = _re.match(r'^([\d.]+)$', raw_str)
                    if m:
                        value = float(m.group(1))
                        unit = 'numeric'

                if value is None:
                    m = _re.search(r'(\d+)', raw_str)
                    if m:
                        value = int(m.group(1))
                        unit = 'extracted_numeric'

                if value is not None:
                    col_suffix = f'_{vc}' if len(val_cols) > 1 else ''
                    rows.append({'param_name': f'{param_name}{col_suffix}', 'value': value, 'unit': unit, 'source': f'{source_file}:{source_sheet}'})

        logger.info(f'Extracted {len(rows)} parameters from table {source_file}:{source_sheet}')
        return rows

    def _extract_params_from_large_table(self, df: pd.DataFrame, source_file: str) -> list:
        """Extract statistical meta-information from large tables.
        Structure-based: analyzes column dtypes and value distributions."""
        import re as _re
        rows = []

        for col in df.columns:
            series = df[col].dropna()
            if len(series) == 0:
                continue

            col_clean = _re.sub(r'[\s/()\-]+', '_', str(col)).strip('_')

            numeric_vals = pd.to_numeric(series, errors='coerce').dropna()
            time_vals = []
            for v in series:
                minutes = self._cell_to_minutes(v)
                if minutes is not None:
                    time_vals.append(minutes)

            if len(numeric_vals) >= len(series) * 0.7 and len(numeric_vals) >= 5:
                rows.append({"param_name": f"stat_min_{col_clean}", "value": round(float(numeric_vals.min()), 2), "unit": "numeric", "source": source_file, "context": f"min of {col} ({len(numeric_vals)} values)"})
                rows.append({"param_name": f"stat_max_{col_clean}", "value": round(float(numeric_vals.max()), 2), "unit": "numeric", "source": source_file, "context": f"max of {col} ({len(numeric_vals)} values)"})
                rows.append({"param_name": f"stat_mean_{col_clean}", "value": round(float(numeric_vals.mean()), 2), "unit": "numeric", "source": source_file, "context": f"mean of {col} ({len(numeric_vals)} values)"})

            elif len(time_vals) >= len(series) * 0.7 and len(time_vals) >= 5:
                t_arr = pd.Series(time_vals)
                rows.append({"param_name": f"stat_earliest_{col_clean}", "value": int(t_arr.min()), "unit": "minutes_from_midnight", "source": source_file, "context": f"earliest time in {col}"})
                rows.append({"param_name": f"stat_latest_{col_clean}", "value": int(t_arr.max()), "unit": "minutes_from_midnight", "source": source_file, "context": f"latest time in {col}"})
                rows.append({"param_name": f"stat_mean_{col_clean}", "value": round(float(t_arr.mean()), 1), "unit": "minutes_from_midnight", "source": source_file, "context": f"mean time in {col}"})

            elif series.dtype == object:
                unique = series.unique()
                if 1 < len(unique) <= 10:
                    for uv in unique:
                        count = int((series == uv).sum())
                        uv_clean = _re.sub(r'[\s/()\-]+', '_', str(uv)).strip('_')[:30]
                        rows.append({"param_name": f"cat_{col_clean}_{uv_clean}", "value": count, "unit": "count", "source": source_file, "context": f"{col}={uv}: {count} occurrences"})

        rows.append({"param_name": "meta_total_records", "value": len(df), "unit": "count", "source": source_file, "context": "total rows in table"})
        logger.info(f"Extracted {len(rows)} meta-parameters from large table {source_file}")
        return rows

    async def _transform_parse_blocks(
        self, upload_dir: Path, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """비정형 블록 구조의 DIA 파싱"""
        file_path = upload_dir / source_file
        if not file_path.exists():
            return None

        df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0, header=None)

        # 빈 행으로 블록 분리
        duties = []
        current_duty = []
        duty_id = 1

        for idx, row in df.iterrows():
            if row.isna().all():
                if current_duty:
                    parsed = self._parse_duty_block(duty_id, current_duty)
                    if parsed:
                        duties.append(parsed)
                        duty_id += 1
                    current_duty = []
            else:
                current_duty.append(row)

        # 마지막 블록
        if current_duty:
            parsed = self._parse_duty_block(duty_id, current_duty)
            if parsed:
                duties.append(parsed)

        return pd.DataFrame(duties) if duties else None

    def _parse_duty_block(self, duty_id: int, rows: list) -> Optional[dict]:
        """개별 DIA 블록을 파싱"""
        if not rows:
            return None

        times = []
        for row in rows:
            for val in row:
                if pd.notna(val):
                    minutes = self._to_minutes(val)
                    if minutes is not None:
                        times.append(minutes)

        if len(times) >= 2:
            return {
                "duty_id": f"D{duty_id:03d}",
                "trip_ids": "",
                "start_time_min": min(times),
                "end_time_min": max(times),
                "duty_type": "normal",
            }
        return None

    def _to_minutes(self, val) -> "Optional[int]":
        """Convert time value to minutes since midnight. Handles time/datetime/str/Timestamp.
        For datetime with year=1900, treats as past-midnight (adds 24*60)."""
        import datetime as _dt
        try:
            if val is None:
                return None
            val_str = str(val).strip().lower()
            if val_str in ("", "nan", "nat", "none"):
                return None

            # datetime or Timestamp with date component
            if hasattr(val, "hour") and hasattr(val, "year"):
                try:
                    yr = val.year
                    if yr == 1900:
                        return 24 * 60 + val.hour * 60 + val.minute
                except (AttributeError, TypeError):
                    pass
                return val.hour * 60 + val.minute

            # datetime.time
            if isinstance(val, _dt.time):
                return val.hour * 60 + val.minute

            # string "HH:MM:SS" or "HH:MM"
            if isinstance(val, str):
                parts = val.strip().split(":")
                if len(parts) >= 2:
                    h, m = int(parts[0]), int(parts[1])
                    if 0 <= h <= 47 and 0 <= m <= 59:
                        return h * 60 + m
            return None
        except Exception:
            return None

    def _format_mapping_result(
        self, auto_confirmed: list, needs_review: list
    ) -> str:
        lines = []
        lines.append("## 데이터 정규화 매핑 결과\n")

        if auto_confirmed:
            lines.append("### 자동 매핑 완료 (확인 불필요)")
            for m in auto_confirmed:
                target = m.get("target_table", "")
                source = m.get("source_file", "")
                sheet = m.get("source_sheet", "")
                conf = m.get("confidence", 0)
                reason = m.get("reason", "")
                source_str = f"{source}:{sheet}" if sheet else source
                lines.append(
                    f"- **{target}** <- {source_str} "
                    f"(확신도: {conf:.0%}) {reason}"
                )
            lines.append("")

        if needs_review:
            lines.append("### 확인 필요")
            for m in needs_review:
                target = m.get("target_table", "")
                source = m.get("source_file", "")
                sheet = m.get("source_sheet", "")
                conf = m.get("confidence", 0)
                reason = m.get("reason", "")
                source_str = f"{source}:{sheet}" if sheet else source
                lines.append(
                    f"- **{target}** <- {source_str} "
                    f"(확신도: {conf:.0%})"
                )
                lines.append(f"  사유: {reason}")
            lines.append("")

        lines.append("---")
        lines.append(
            "**확인**을 입력하면 변환을 실행합니다. "
            "**수정**을 입력하면 매핑을 조정할 수 있습니다."
        )

        return "\n".join(lines)


# ── 모듈 레벨 함수 ──
_skill_instance: Optional[DataNormalizationSkill] = None


def get_skill() -> DataNormalizationSkill:
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = DataNormalizationSkill()
    return _skill_instance


async def skill_data_normalization(
    model, session: CrewSession, project_id: str,
    message: str, params: Dict
) -> Dict:
    skill = get_skill()
    return await skill.handle(model, session, project_id, message, params)
