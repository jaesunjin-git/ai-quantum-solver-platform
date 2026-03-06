from __future__ import annotations
"""
domains/crew/skills/data_normalization.py

Data Normalization Skill (Phase 2: Semantic Normalization).

TASK 6 리팩토링:
  - Phase 1 결과(phase1/)를 기반으로 동작 (중복 변환 제거)
  - LLM 매핑은 Phase 1에서 처리하지 못한 파일에만 적용
  - confirmed_problem의 파라미터를 최종 parameters.csv에 병합
  - _format_mapping_result, _transform_parse_blocks, _extract_params_from_large_table 구현
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


def _get_safe_dir(project_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(project_id))
    return _UPLOAD_BASE / safe_id


class DataNormalizationSkill:

    def __init__(self):
        self.config = _load_yaml("prompts/data_normalization.yaml")
        self.confidence_threshold = self.config.get("confidence_threshold", 0.8)
        self.data_detection = _load_yaml("knowledge/data_detection.yaml")

    # ══════════════════════════════════════
    # Public entry point
    # ══════════════════════════════════════
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

        # 첫 진입: Phase 1 결과 확인 + LLM 매핑 생성
        phase1_dir = _get_safe_dir(project_id) / "phase1"
        has_phase1_trips = (phase1_dir / "timetable_rows.csv").exists()
        has_phase1_params = (phase1_dir / "parameters_raw.csv").exists()

        mapping_result = await self._generate_mapping(model, state, has_phase1_trips, has_phase1_params)

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

    # ══════════════════════════════════════
    # 매핑 결과 포맷팅
    # ══════════════════════════════════════
    def _format_mapping_result(self, auto_confirmed: list, needs_review: list) -> str:
        """매핑 결과를 사용자에게 보여줄 마크다운 텍스트로 변환"""
        lines = ["## 데이터 정규화 매핑 결과\n"]

        if auto_confirmed:
            lines.append(f"### 자동 확정 ({len(auto_confirmed)}건)\n")
            for m in auto_confirmed:
                target = m.get("target_table", "?")
                source = m.get("source_file", "?")
                sheet = m.get("source_sheet", "")
                conf = m.get("confidence", 0)
                reason = m.get("reason", "")
                transform = m.get("transform_type", "")
                src_display = f"{source}" + (f" [{sheet}]" if sheet else "")
                lines.append(
                    f"- **{target}** ← {src_display} "
                    f"(confidence: {conf:.0%}, transform: {transform})"
                )
                if reason:
                    lines.append(f"  - {reason}")
            lines.append("")

        if needs_review:
            lines.append(f"### 확인 필요 ({len(needs_review)}건)\n")
            for m in needs_review:
                target = m.get("target_table", "?")
                source = m.get("source_file", "?")
                sheet = m.get("source_sheet", "")
                conf = m.get("confidence", 0)
                reason = m.get("reason", "")
                transform = m.get("transform_type", "")
                src_display = f"{source}" + (f" [{sheet}]" if sheet else "")
                lines.append(
                    f"- **{target}** ← {src_display} "
                    f"(confidence: {conf:.0%}, transform: {transform})"
                )
                if reason:
                    lines.append(f"  - {reason}")
            lines.append("")
            lines.append("위 매핑을 **확인** 또는 **수정** 해주세요.")

        return "\n".join(lines)

    # ══════════════════════════════════════
    # LLM 매핑 생성 (Phase 1 결과 반영)
    # ══════════════════════════════════════
    async def _generate_mapping(
        self, model, state, has_phase1_trips: bool, has_phase1_params: bool
    ) -> Optional[dict]:
        confirmed = state.confirmed_problem or {}
        stage = confirmed.get("stage", "task_generation")
        required = self.config.get("required_tables", {}).get(stage, {})

        # Phase 1 결과를 자동 매핑으로 시작
        auto_mappings = []

        if has_phase1_trips:
            auto_mappings.append({
                "target_table": "trips",
                "source_file": "phase1/timetable_rows.csv",
                "source_sheet": "",
                "transform_type": "phase1_copy",
                "confidence": 1.0,
                "reason": "Phase 1에서 이미 변환된 시간표 데이터 사용",
                "column_mapping": {},
            })

        if has_phase1_params:
            auto_mappings.append({
                "target_table": "parameters",
                "source_file": "phase1/parameters_raw.csv",
                "source_sheet": "",
                "transform_type": "phase1_copy",
                "confidence": 0.9,
                "reason": "Phase 1에서 추출된 파라미터 데이터 (confirmed_problem과 병합)",
                "column_mapping": {},
            })

        # confirmed_problem 파라미터 매핑
        cp = confirmed.get("parameters", {})
        if cp:
            auto_mappings.append({
                "target_table": "parameters",
                "source_file": "",
                "source_sheet": "",
                "transform_type": "from_confirmed",
                "confidence": 1.0,
                "reason": "문제 정의에서 확정된 파라미터",
                "column_mapping": {},
            })

        # Phase 1에서 처리하지 못한 파일이 있는지 확인
        unprocessed_files = self._find_unprocessed_files(state)

        if unprocessed_files and model:
            # LLM에게 미처리 파일에 대한 추가 매핑만 요청
            llm_mappings = await self._generate_llm_mapping_for_remaining(
                model, state, unprocessed_files, required
            )
            if llm_mappings:
                auto_mappings.extend(llm_mappings)

        return {"mappings": auto_mappings}

    def _find_unprocessed_files(self, state) -> list:
        """Phase 1에서 처리되지 않은 파일 목록을 반환"""
        phase1_summary = state.phase1_summary or {}
        processed_files = set()
        for fe in phase1_summary.get("files_processed", []):
            name = fe.get("name", "")
            structure = fe.get("structure", "")
            # Phase 1에서 실제로 변환된 파일은 제외
            sheets = fe.get("sheets", [])
            if sheets:
                for sh in sheets:
                    if sh.get("structure") in ("pivot_timetable", "small_kv"):
                        processed_files.add(name)
            elif structure in ("text", "pivot_timetable", "small_kv"):
                processed_files.add(name)

        # 업로드 파일 중 미처리 파일
        unprocessed = []
        for f in (state.uploaded_files or []):
            fname = f if isinstance(f, str) else f.get("name", "")
            if fname and fname not in processed_files:
                ext = Path(fname).suffix.lower()
                # PDF, DOCX, HWP 등 Phase 1에서 skip된 파일
                if ext in (".pdf", ".docx", ".doc", ".hwp", ".hwpx"):
                    unprocessed.append(fname)
                # tabular_regular도 Phase 1에서 skip됨
                elif ext in (".xlsx", ".xls", ".csv", ".tsv"):
                    unprocessed.append(fname)
        return unprocessed

    async def _generate_llm_mapping_for_remaining(
        self, model, state, unprocessed_files: list, required: dict
    ) -> list:
        """LLM에게 미처리 파일의 매핑만 요청"""
        confirmed = state.confirmed_problem or {}
        problem_summary = json.dumps(confirmed, ensure_ascii=False, indent=2)[:2000]

        system = self.config.get("system_prompt", "")
        rules = self.config.get("rules", [])
        rules_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))

        # 필요한 테이블 설명
        tables_desc = ""
        for table_name, table_info in required.items():
            cols = table_info.get("columns", [])
            desc = table_info.get("description", "")
            tables_desc += f"\n  - {table_name}: {desc} (columns: {cols})"

        # 파일 인벤토리 (미처리 파일만)
        file_inventory = ""
        upload_dir = _get_safe_dir(str(state.project_id)) if state.project_id else None
        if upload_dir and upload_dir.exists():
            for fname in unprocessed_files:
                fp = upload_dir / fname
                if not fp.exists():
                    continue
                ext = fp.suffix.lower()
                if ext in (".xlsx", ".xls"):
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(str(fp), read_only=True)
                        for sh in wb.sheetnames:
                            file_inventory += f"\n  - source_file: {fname}  source_sheet: {sh}"
                        wb.close()
                    except Exception:
                        file_inventory += f"\n  - source_file: {fname}"
                else:
                    file_inventory += f"\n  - source_file: {fname}  source_sheet: null"

        if not file_inventory.strip():
            return []

        # 데이터 프로필
        profile_summary = ""
        if state.csv_summary:
            profile_summary = state.csv_summary[:3000]

        prompt = f"""{system}

Rules:
{rules_text}

NOTE: The following files have already been processed by Phase 1:
- trips data (timetable_rows.csv) is already available
- basic parameters (parameters_raw.csv) are already extracted
Only generate mappings for the REMAINING unprocessed files below.
Do NOT map files to 'trips' if trips are already handled by Phase 1.

[Confirmed Problem Definition]
{problem_summary}

[Required Tables]
{tables_desc}

[Unprocessed Files - THESE NEED MAPPING]
{file_inventory}

[Data Summary]
{profile_summary}

Generate mapping JSON for these unprocessed files only."""

        try:
            response = await asyncio.to_thread(model.generate_content, prompt)
            text = response.text.strip()

            # JSON 클리닝
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip().rsplit("```", 1)[0]
            text = text.strip()

            json_match = re.search(r"\{[\s\S]*\}", text)
            raw_json = json_match.group() if json_match else text

            # 잘못된 이스케이프 수정
            cleaned = []
            i = 0
            while i < len(raw_json):
                if raw_json[i] == "\\" and i + 1 < len(raw_json):
                    nxt = raw_json[i + 1]
                    if nxt in '"/\\bfnrtu':
                        cleaned.append(raw_json[i])
                        cleaned.append(nxt)
                        i += 2
                        continue
                    else:
                        cleaned.append("\\\\")
                        i += 1
                        continue
                cleaned.append(raw_json[i])
                i += 1
            raw_json = "".join(cleaned)

            result = json.loads(raw_json)
            return result.get("mappings", [])

        except Exception as e:
            logger.error(f"LLM mapping for remaining files failed: {e}", exc_info=True)
            return []

    # ══════════════════════════════════════
    # 사용자 응답 처리
    # ══════════════════════════════════════
    async def _handle_user_response(
        self, model, session: CrewSession, project_id: str, message: str
    ) -> Dict:
        state = session.state
        keywords = self.config.get("confirmation_keywords", {})
        msg_lower = message.strip().lower()

        positive = [k.lower() for k in keywords.get("positive", [])]
        modify = [k.lower() for k in keywords.get("modify", [])]
        restart = [k.lower() for k in keywords.get("restart", [])]

        # 확인 -> 변환 실행
        if any(kw in msg_lower for kw in positive):
            return await self._execute_normalization(model, session, project_id)

        # 수정
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

        # 기타: LLM에 수정 요청 전달
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
            response = await asyncio.to_thread(model.generate_content, modify_prompt)
            text = response.text.strip()

            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip().rsplit("```", 1)[0]
            text = text.strip()

            json_match = re.search(r"\{[\s\S]*\}", text)
            raw = json_match.group() if json_match else text
            updated = json.loads(raw)

            auto_confirmed = []
            needs_review = []
            for m in updated.get("mappings", []):
                if m.get("confidence", 0) >= self.confidence_threshold:
                    auto_confirmed.append(m)
                else:
                    needs_review.append(m)

            state.normalization_mapping = {
                "auto_confirmed": auto_confirmed,
                "needs_review": needs_review,
                "all_mappings": updated.get("mappings", []),
            }
            save_session_state(project_id, state)

            response_text = self._format_mapping_result(auto_confirmed, needs_review)
            return {
                "type": "data_normalization",
                "text": "매핑이 수정되었습니다.\n\n" + response_text,
                "data": {
                    "view_mode": "normalization_mapping",
                    "mappings": {"auto_confirmed": auto_confirmed, "needs_review": needs_review},
                    "agent_status": "normalization_proposed",
                },
                "options": [
                    {"label": "확인", "action": "send", "message": "확인"},
                    {"label": "수정", "action": "send", "message": "수정"},
                ],
            }

        except Exception as e:
            logger.error(f"Mapping modification failed: {e}", exc_info=True)
            return {
                "type": "data_normalization",
                "text": f"매핑 수정에 실패했습니다: {e}\n\n**확인**, **수정**, 또는 **다시**를 입력해주세요.",
                "data": {"agent_status": "awaiting_response"},
                "options": [
                    {"label": "확인", "action": "send", "message": "확인"},
                    {"label": "수정", "action": "send", "message": "수정"},
                ],
            }

    # ══════════════════════════════════════
    # 변환 실행 (Phase 1 활용)
    # ══════════════════════════════════════
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
        upload_dir = _get_safe_dir(project_id)
        phase1_dir = upload_dir / "phase1"
        norm_dir = upload_dir / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)

        results = []
        errors = []

        for m in all_mappings:
            target = m.get("target_table", "")
            transform = m.get("transform_type", "")
            source_file = m.get("source_file", "")
            source_sheet = m.get("source_sheet", "")

            try:
                # ═══ PHASE 1 COPY (trips / parameters) ═══
                if transform == "phase1_copy":
                    src_path = upload_dir / source_file
                    if src_path.exists():
                        df = pd.read_csv(str(src_path), encoding="utf-8")
                        if target == "trips":
                            out_path = norm_dir / "trips.csv"
                            df.to_csv(str(out_path), index=False, encoding="utf-8")
                            results.append(f"trips.csv: {len(df)} rows (from Phase 1)")
                            # overlap_pairs.json도 복사
                            _op_src = upload_dir / "phase1" / "overlap_pairs.json"
                            if _op_src.exists():
                                import shutil
                                shutil.copy2(str(_op_src), str(norm_dir / "overlap_pairs.json"))
                                results.append("overlap_pairs.json copied from Phase 1")
                        elif target == "parameters":
                            out_path = norm_dir / "parameters.csv"
                            # 기존 파라미터가 있으면 병합
                            if out_path.exists():
                                existing = pd.read_csv(str(out_path), encoding="utf-8")
                                if "param_name" in df.columns and "param_name" in existing.columns:
                                    existing = existing[~existing["param_name"].isin(df["param_name"])]
                                df = pd.concat([existing, df], ignore_index=True)
                            df.to_csv(str(out_path), index=False, encoding="utf-8")
                            results.append(f"parameters.csv: {len(df)} rows (from Phase 1)")
                    else:
                        errors.append(f"{target}: Phase 1 file not found ({source_file})")

                # ═══ FROM CONFIRMED PROBLEM ═══
                elif transform == "from_confirmed":
                    confirmed = state.confirmed_problem or {}
                    params = confirmed.get("parameters", {})
                    if params:
                        # Phase 1 파라미터를 fallback으로 로드
                        _p1_values = {}
                        _p1_path = upload_dir / "phase1" / "parameters_raw.csv"
                        if _p1_path.exists():
                            try:
                                _p1_df = pd.read_csv(str(_p1_path), dtype=str)
                                if "semantic_id" in _p1_df.columns and "value" in _p1_df.columns:
                                    for _, _r in _p1_df.iterrows():
                                        _sid = str(_r.get("semantic_id", ""))
                                        _val = str(_r.get("value", ""))
                                        if _sid and _val and _val != "nan":
                                            _p1_values[_sid] = _val
                            except Exception:
                                pass
                        rows = []
                        for pname, pinfo in params.items():
                            value = pinfo.get("value") if isinstance(pinfo, dict) else pinfo
                            src = pinfo.get("source", "confirmed") if isinstance(pinfo, dict) else "confirmed"
                            # value가 None이면 Phase 1에서 fallback
                            if value is None or (isinstance(value, float) and str(value) == "nan"):
                                if pname in _p1_values:
                                    value = _p1_values[pname]
                                    src = "phase1_fallback"
                            rows.append({"param_name": pname, "value": value, "unit": "minutes", "source": src, "semantic_id": pname})
                        if rows:
                            df = pd.DataFrame(rows)
                            out_path = norm_dir / "parameters.csv"
                            if out_path.exists():
                                existing = pd.read_csv(str(out_path), encoding="utf-8")
                                if "param_name" in existing.columns:
                                    existing = existing[~existing["param_name"].isin(df["param_name"])]
                                df = pd.concat([existing, df], ignore_index=True)
                            df.to_csv(str(out_path), index=False, encoding="utf-8")
                            results.append(f"parameters.csv: +{len(rows)} confirmed params")

                # ═══ DIRECT (이미 정규형 CSV/Excel) ═══
                elif transform == "direct":
                    df = await self._transform_direct(
                        upload_dir, source_file, source_sheet, m
                    )
                    if df is not None and len(df) > 0:
                        out_name = f"{target}.csv"
                        out_path = norm_dir / out_name
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"{out_name}: {len(df)} rows")

                # ═══ EXTRACT_KV (소규모 테이블 -> 파라미터) ═══
                elif transform == "extract_kv":
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
                        results.append(f"parameters.csv: +{len(df)} rows from {source_file}")

                # ═══ PARSE_BLOCKS (기존 DIA/듀티표) ═══
                elif transform == "parse_blocks":
                    df = await self._transform_parse_blocks(
                        upload_dir, source_file, source_sheet, m
                    )
                    if df is not None and len(df) > 0:
                        out_path = norm_dir / f"{target}.csv"
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"{target}.csv: {len(df)} rows")

                # ═══ UNPIVOT (LLM이 제안한 경우, Phase 1 미통과 파일) ═══
                elif transform == "unpivot":
                    df = await self._transform_unpivot_timetable(
                        upload_dir, source_file, source_sheet, m
                    )
                    if df is not None and len(df) > 0:
                        out_path = norm_dir / "trips.csv"
                        if out_path.exists():
                            existing = pd.read_csv(str(out_path), encoding="utf-8")
                            df = pd.concat([existing, df], ignore_index=True)
                        df.to_csv(str(out_path), index=False, encoding="utf-8")
                        results.append(f"trips.csv: {len(df)} rows (unpivot)")

            except Exception as e:
                logger.error(f"Transform error [{target}] {source_file}: {e}", exc_info=True)
                errors.append(f"{target}: {str(e)}")

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

    # ══════════════════════════════════════
    # 변환 함수들
    # ══════════════════════════════════════

    def _to_minutes(self, val) -> Optional[float]:
        """셀 값을 분(minutes) 단위로 변환"""
        import datetime as _dt
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, _dt.time):
            return val.hour * 60 + val.minute
        if isinstance(val, _dt.datetime):
            return val.hour * 60 + val.minute
        s = str(val).strip()
        m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return h * 60 + mi
        m = re.match(r"(\d+)\s*(?:시간|hours?|hrs?)\s*(?:(\d+)\s*(?:분|minutes?|mins?))?", s, re.IGNORECASE)
        if m:
            return int(m.group(1)) * 60 + (int(m.group(2)) if m.group(2) else 0)
        m = re.match(r"(\d+)\s*(?:분|minutes?|mins?)$", s, re.IGNORECASE)
        if m:
            return int(m.group(1))
        try:
            v = float(s)
            if 0 <= v <= 2000:
                return v
        except ValueError:
            pass
        return None

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
        elif ext == ".tsv":
            try:
                df = pd.read_csv(str(file_path), sep="\t", encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(str(file_path), sep="\t", encoding="cp949")
        else:
            df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)

        col_mapping = mapping.get("column_mapping", {})
        if col_mapping:
            reverse_map = {v: k for k, v in col_mapping.items() if isinstance(v, str)}
            df = df.rename(columns=reverse_map)

        return df

    async def _transform_unpivot_timetable(
        self, upload_dir, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """피벗 시간표 -> 행 기반 trip (Phase 1 로직 재사용)"""
        file_path = Path(upload_dir) / source_file
        if not file_path.exists():
            return None

        ext = file_path.suffix.lower()
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)
        elif ext == ".csv":
            try:
                df = pd.read_csv(str(file_path), encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(str(file_path), encoding="cp949")
        else:
            return None

        if df is None or df.empty:
            return None

        # 컬럼 분류
        time_cols, non_time_cols = [], []
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

        # 역 vs 메타 분리
        station_cols, meta_cols = [], []
        for col in time_cols:
            sample = df[col].dropna().head(20)
            vals = [self._to_minutes(v) for v in sample]
            valid = [m for m in vals if m is not None]
            if len(valid) < 3:
                meta_cols.append(col)
                continue
            mean_val = sum(valid) / len(valid)
            spread = max(valid) - min(valid)
            non_time_str = sum(1 for v in df[col].dropna() if pd.notna(v) and self._to_minutes(v) is None)
            if mean_val > 60 and spread > 120 and non_time_str == 0:
                station_cols.append(col)
            else:
                meta_cols.append(col)

        # 상행/하행
        fwd = [c for c in station_cols if ".1" not in str(c)]
        rev = [c for c in station_cols if ".1" in str(c)]
        if not rev and len(station_cols) > 10:
            seen = {}
            fwd, rev = [], []
            for col in station_cols:
                base = str(col).split(".")[0].strip()
                if base in seen:
                    rev.append(col)
                else:
                    seen[base] = col
                    fwd.append(col)
        has_round = len(rev) >= 3

        # ID 컬럼
        id_col, id_col_rev = None, None
        for col in non_time_cols:
            nn = df[col].dropna().head(10)
            if len(nn) == 0:
                continue
            try:
                vals = pd.to_numeric(nn, errors="coerce")
                if vals.notna().sum() >= 8 and (vals % 1 == 0).all():
                    if id_col is None:
                        id_col = col
                    elif id_col_rev is None:
                        id_col_rev = col
                        break
            except Exception:
                continue

        # Trip 추출
        trips = []
        for idx, row in df.iterrows():
            if fwd:
                tid = row.get(id_col) if id_col else idx * 2 + 1
                if pd.notna(tid):
                    times = [(str(st), self._to_minutes(row.get(st)))
                             for st in fwd if pd.notna(row.get(st)) and self._to_minutes(row.get(st)) is not None]
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid) if not isinstance(tid, str) else tid,
                            "direction": "forward",
                            "dep_station": times[0][0], "arr_station": times[-1][0],
                            "trip_dep_time": times[0][1], "trip_arr_time": times[-1][1],
                            "trip_duration": times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })
            if has_round and rev:
                tid_r = row.get(id_col_rev) if id_col_rev else (
                    int(row.get(id_col, 0)) + 1 if id_col and pd.notna(row.get(id_col)) else idx * 2 + 2
                )
                if pd.notna(tid_r):
                    times = [(str(st).replace(".1", "").strip(), self._to_minutes(row.get(st)))
                             for st in rev if pd.notna(row.get(st)) and self._to_minutes(row.get(st)) is not None]
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid_r) if not isinstance(tid_r, str) else tid_r,
                            "direction": "reverse",
                            "dep_station": times[0][0], "arr_station": times[-1][0],
                            "trip_dep_time": times[0][1], "trip_arr_time": times[-1][1],
                            "trip_duration": times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })

        if not trips:
            return None
        result = pd.DataFrame(trips).sort_values("trip_dep_time").reset_index(drop=True)
        logger.info(f"Unpivot: {len(result)} trips extracted from {source_file}")
        return result

    async def _transform_parse_blocks(
        self, upload_dir: Path, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """
        블록 구조 데이터를 파싱한다.
        DIA(듀티표) 등 비정형 테이블에서 듀티 블록을 추출.
        빈 행 또는 구분자 행으로 블록을 분리한다.
        """
        file_path = Path(upload_dir) / source_file
        if not file_path.exists():
            return None

        ext = file_path.suffix.lower()
        if ext == ".csv":
            try:
                df = pd.read_csv(str(file_path), encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(str(file_path), encoding="cp949")
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)
        else:
            return None

        if df is None or df.empty:
            return None

        # 전략: 연속된 비빈 행을 하나의 블록(듀티)으로 그룹화
        blocks = []
        current_block = []

        for idx, row in df.iterrows():
            if row.isna().all() or (row.astype(str).str.strip() == "").all():
                if current_block:
                    blocks.append(current_block)
                    current_block = []
            else:
                current_block.append(row.to_dict())

        if current_block:
            blocks.append(current_block)

        if not blocks:
            return None

        # 각 블록에서 듀티 정보 추출
        duties = []
        for block_idx, block in enumerate(blocks):
            duty = {"duty_id": block_idx + 1, "trip_count": len(block)}

            # 시간 정보 추출
            all_times = []
            trip_ids = []
            for row in block:
                for col, val in row.items():
                    m = self._to_minutes(val)
                    if m is not None:
                        all_times.append(m)
                    # trip ID 감지
                    if isinstance(val, (int, float)) and not pd.isna(val):
                        try:
                            iv = int(val)
                            if 1 <= iv <= 9999:
                                trip_ids.append(iv)
                        except (ValueError, OverflowError):
                            pass

            if all_times:
                duty["start_time_min"] = min(all_times)
                duty["end_time_min"] = max(all_times)
                duty["duration_min"] = max(all_times) - min(all_times)
            if trip_ids:
                duty["trip_ids"] = str(sorted(set(trip_ids)))

            duties.append(duty)

        result = pd.DataFrame(duties)
        logger.info(f"ParseBlocks: {len(result)} blocks from {source_file}")
        return result

    async def _transform_parameters(
        self, state, upload_dir: Path, source_file: str,
        source_sheet: str, mapping: dict
    ) -> Optional[pd.DataFrame]:
        """파라미터 추출 - 파일 구조에 따라 전략 결정"""
        rows = []
        transform = mapping.get("transform_type", "")

        if not source_file or transform == "from_confirmed":
            confirmed = getattr(state, "confirmed_problem", None) or {}
            params = confirmed.get("parameters", {})
            for pname, pinfo in params.items():
                value = pinfo.get("value") if isinstance(pinfo, dict) else pinfo
                src = pinfo.get("source", "confirmed") if isinstance(pinfo, dict) else "confirmed"
                rows.append({"param_name": pname, "value": value, "unit": "minutes", "source": src, "semantic_id": pname})
            return pd.DataFrame(rows) if rows else None

        file_path = upload_dir / source_file
        if not file_path.exists():
            return None

        try:
            ext = file_path.suffix.lower()

            if ext in (".txt", ".md", ".text"):
                rows.extend(self._extract_params_from_text(file_path))

            elif ext in (".xlsx", ".xls", ".csv", ".tsv"):
                if ext == ".csv":
                    df = pd.read_csv(str(file_path))
                elif ext == ".tsv":
                    df = pd.read_csv(str(file_path), sep="\t")
                else:
                    df = pd.read_excel(str(file_path), sheet_name=source_sheet or 0)

                n_rows = len(df)
                if n_rows <= 30:
                    rows.extend(self._extract_params_from_table(df, source_file, source_sheet))
                else:
                    rows.extend(self._extract_params_from_large_table(df, source_file))

        except Exception as e:
            logger.error(f"Parameter extraction error from {source_file}: {e}", exc_info=True)

        return pd.DataFrame(rows) if rows else None

    def _extract_params_from_text(self, file_path: Path) -> list:
        """텍스트에서 숫자+단위 패턴으로 파라미터 추출"""
        rows = []
        text = None
        for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if not text:
            return rows

        param_idx = 0
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            extracted = []

            for m in re.finditer(r"(\d+)\s*(?:시간|hours?|hrs?)\s*(?:(\d+)\s*(?:분|minutes?|mins?))?", line, re.IGNORECASE):
                hrs = int(m.group(1))
                mins = int(m.group(2)) if m.group(2) else 0
                extracted.append(("duration", hrs * 60 + mins, "minutes", m.start(), m.end()))

            for m in re.finditer(r"(\d+)\s*(?:분|minutes?|mins?)(?!\w)", line, re.IGNORECASE):
                val = int(m.group(1))
                pos = m.start()
                if not any(pos >= e[3] and pos <= e[4] for e in extracted):
                    extracted.append(("duration", val, "minutes", pos, m.end()))

            for m in re.finditer(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", line):
                h, mi = int(m.group(1)), int(m.group(2))
                if 0 <= h <= 23 and 0 <= mi <= 59:
                    extracted.append(("time_of_day", h * 60 + mi, "minutes_from_midnight", m.start(), m.end()))

            for m in re.finditer(r"(\d+)\s*(?:개|ea|units?|trips?|duties?|services?)(?:\s|$|[^\w])", line, re.IGNORECASE):
                extracted.append(("count", int(m.group(1)), "count", m.start(), m.end()))

            for ptype, value, unit, _, _ in extracted:
                rows.append({
                    "param_name": f"{ptype}_{param_idx}",
                    "value": value,
                    "unit": unit,
                    "source": f"text:{file_path.name}",
                    "context": line[:120],
                    "param_type": ptype,
                })
                param_idx += 1

        return rows

    def _extract_params_from_table(self, df: pd.DataFrame, source_file: str, source_sheet: str) -> list:
        """소규모 테이블에서 key-value 파라미터 추출"""
        rows = []
        if df.empty or len(df.columns) < 2:
            return rows

        # key-value 패턴: 텍스트 컬럼 + 숫자 컬럼
        text_col, val_col = None, None
        for col in df.columns:
            sample = df[col].dropna()
            if len(sample) == 0:
                continue
            numeric_ratio = sum(
                1 for v in sample
                if self._to_minutes(v) is not None or isinstance(v, (int, float))
            ) / len(sample)
            if numeric_ratio < 0.3 and text_col is None:
                text_col = col
            elif numeric_ratio >= 0.5 and val_col is None:
                val_col = col

        if text_col is not None and val_col is not None:
            for _, row in df.iterrows():
                key = str(row.get(text_col, "")).strip()
                val = row.get(val_col)
                if key and pd.notna(val):
                    minutes = self._to_minutes(val)
                    rows.append({
                        "param_name": key,
                        "value": minutes if minutes is not None else val,
                        "unit": "minutes" if minutes is not None else "raw",
                        "source": f"{source_file}:{source_sheet}",
                    })
        else:
            # 크로스탭: 각 셀을 파라미터로
            for _, row in df.iterrows():
                for col in df.columns:
                    val = row.get(col)
                    if pd.notna(val):
                        minutes = self._to_minutes(val)
                        if minutes is not None:
                            rows.append({
                                "param_name": str(col),
                                "value": minutes,
                                "unit": "minutes",
                                "source": f"{source_file}:{source_sheet}",
                            })

        return rows

    def _extract_params_from_large_table(self, df: pd.DataFrame, source_file: str) -> list:
        """
        대규모 테이블에서 통계 기반 파라미터 추출.
        30행 초과 테이블은 개별 셀이 아닌 컬럼 통계를 파라미터로 사용.
        """
        rows = []
        if df.empty:
            return rows

        for col in df.columns:
            series = df[col].dropna()
            if len(series) == 0:
                continue

            # 시간 컬럼 감지
            time_vals = [self._to_minutes(v) for v in series.head(20)]
            valid_times = [t for t in time_vals if t is not None]

            if len(valid_times) >= len(time_vals) * 0.5 and valid_times:
                # 시간 컬럼: min, max, mean, count
                all_times = [self._to_minutes(v) for v in series]
                all_valid = [t for t in all_times if t is not None]
                if all_valid:
                    col_name = str(col).strip()
                    rows.append({"param_name": f"{col_name}_min", "value": min(all_valid), "unit": "minutes", "source": source_file})
                    rows.append({"param_name": f"{col_name}_max", "value": max(all_valid), "unit": "minutes", "source": source_file})
                    rows.append({"param_name": f"{col_name}_mean", "value": round(sum(all_valid) / len(all_valid), 1), "unit": "minutes", "source": source_file})
                    rows.append({"param_name": f"{col_name}_count", "value": len(all_valid), "unit": "count", "source": source_file})
            elif series.dtype in ("int64", "float64"):
                # 일반 숫자 컬럼: 기본 통계
                col_name = str(col).strip()
                rows.append({"param_name": f"{col_name}_min", "value": float(series.min()), "unit": "raw", "source": source_file})
                rows.append({"param_name": f"{col_name}_max", "value": float(series.max()), "unit": "raw", "source": source_file})
                rows.append({"param_name": f"{col_name}_mean", "value": round(float(series.mean()), 2), "unit": "raw", "source": source_file})

        logger.info(f"Large table params: {len(rows)} from {source_file}")
        return rows


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