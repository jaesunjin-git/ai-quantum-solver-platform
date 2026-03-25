from __future__ import annotations
"""
domains/crew/skills/structural_normalization.py

Structural Normalization Skill (Phase 1).

문제 정의(Problem Definition) 이전에 실행.
LLM 호출 없이 코드 기반으로 데이터의 물리적 형태를 변환한다.

역할:
  - 피벗 시간표 -> 행 기반 테이블 (unpivot)
  - 텍스트/소규모 테이블 -> key-value 파라미터 추출
  - 인코딩 통일 (CP949 -> UTF-8)
  - 시간 형식 통일 (HH:MM, datetime -> 분 단위)
  - 구조 판별 리포트 생성

입력: uploads/{project_id}/ 의 원본 파일
출력: uploads/{project_id}/phase1/ 의 정규화된 CSV + structure_report.json
"""

import asyncio
import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from domains.crew.session import CrewSession, save_session_state

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[3]
_UPLOAD_BASE = _BASE / "uploads"


# ════════════════════════════════════════
# 유틸: 시간 변환 (LLM 불필요, 패턴 기반)
# ════════════════════════════════════════

def _to_minutes(val) -> Optional[float]:
    """셀 값을 분(minutes) 단위 숫자로 변환. None이면 변환 불가."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime.time):
        return val.hour * 60 + val.minute
    if isinstance(val, datetime.datetime):
        return val.hour * 60 + val.minute
    s = str(val).strip()
    # HH:MM or HH:MM:SS
    m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h * 60 + mi
    # N시간 M분 / N hours M minutes
    m = re.match(
        r"(\d+)\s*(?:시간|hours?|hrs?)\s*(?:(\d+)\s*(?:분|minutes?|mins?))?",
        s, re.IGNORECASE,
    )
    if m:
        return int(m.group(1)) * 60 + (int(m.group(2)) if m.group(2) else 0)
    # N분 / N minutes
    m = re.match(r"(\d+)\s*(?:분|minutes?|mins?)$", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 순수 숫자 (이미 분 단위일 수 있음)
    try:
        v = float(s)
        if 0 <= v <= 2000:
            return v
    except ValueError:
        pass
    return None


def _fix_midnight_wrap(times: list) -> list:
    """
    역 시퀀스의 자정 넘김 보정.

    열차가 23:50 → 00:05 식으로 자정을 넘기면
    _to_minutes()가 [1430, 5]로 변환하여 시간이 감소함.
    이전 역보다 시각이 줄어들면 +1440(24시간)을 더해 연속성 보장.

    Args:
        times: [(station_name, minutes), ...] 순서대로

    Returns:
        보정된 times 리스트
    """
    if len(times) <= 1:
        return times
    result = [times[0]]
    for i in range(1, len(times)):
        st, m = times[i]
        prev_m = result[-1][1]
        if m < prev_m:
            m += 1440
        result.append((st, m))
    return result


def _read_file(file_path: Path, sheet=None) -> Optional[pd.DataFrame]:
    """파일을 DataFrame으로 읽기. 인코딩 자동 감지."""
    ext = file_path.suffix.lower()
    if ext == ".csv":
        for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
            try:
                return pd.read_csv(str(file_path), encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return None
    elif ext in (".xlsx", ".xls"):
        engine = "openpyxl" if ext == ".xlsx" else "xlrd"
        try:
            return pd.read_excel(str(file_path), sheet_name=sheet or 0, engine=engine)
        except Exception:
            try:
                alt = "xlrd" if engine == "openpyxl" else "openpyxl"
                return pd.read_excel(str(file_path), sheet_name=sheet or 0, engine=alt)
            except Exception as e:
                logger.error(f"Excel read failed {file_path}: {e}")
                return None
    return None


def _read_text_safe(file_path: Path) -> Optional[str]:
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def _get_safe_dir(project_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(project_id))
    return _UPLOAD_BASE / safe_id


# ════════════════════════════════════════
# 구조 판별기 (Structure Detector)
# ════════════════════════════════════════

class StructureDetector:
    """파일/시트의 데이터 구조를 판별한다. LLM 불필요."""

    @staticmethod
    def classify(df: pd.DataFrame) -> str:
        """
        Returns: 'pivot_timetable' | 'tabular_regular' | 'small_kv' | 'non_tabular'
        """
        if df is None or df.empty:
            return "non_tabular"

        n_rows, n_cols = df.shape

        # 소규모 key-value 테이블
        if n_rows <= 30 and n_cols <= 6:
            return "small_kv"

        # 피벗 시간표 감지: .1 접미사 컬럼 + 시간값 컬럼이 많음
        cols = [str(c) for c in df.columns]
        suffix_count = sum(1 for c in cols if ".1" in c)

        time_col_count = 0
        for col in df.columns:
            sample = df[col].dropna().head(10)
            if len(sample) == 0:
                continue
            tc = sum(1 for v in sample if _to_minutes(v) is not None)
            if len(sample) > 0 and tc / len(sample) >= 0.7:
                time_col_count += 1

        if suffix_count >= 3 and time_col_count >= 10:
            return "pivot_timetable"
        if time_col_count >= n_cols * 0.6 and n_cols > 10:
            return "pivot_timetable"

        return "tabular_regular"


# ════════════════════════════════════════
# 변환기 (Transformers)
# ════════════════════════════════════════

class PivotUnpivoter:
    """피벗 시간표를 행 기반 trip 테이블로 변환."""

    @staticmethod
    def transform(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None

        # Step 1: 컬럼을 시간/비시간으로 분류
        time_cols, non_time_cols = [], []
        for col in df.columns:
            sample = df[col].dropna().head(30)
            if len(sample) == 0:
                non_time_cols.append(col)
                continue
            tc = sum(1 for v in sample if _to_minutes(v) is not None)
            if tc / len(sample) >= 0.7:
                time_cols.append(col)
            else:
                non_time_cols.append(col)

        # Step 2: 역 컬럼 vs 메타 시간 컬럼 분리
        station_cols, meta_cols = [], []
        for col in time_cols:
            sample = df[col].dropna().head(20)
            vals = [_to_minutes(v) for v in sample]
            valid = [m for m in vals if m is not None]
            if len(valid) < 3:
                meta_cols.append(col)
                continue
            mean_val = sum(valid) / len(valid)
            spread = max(valid) - min(valid)
            non_time_str = sum(
                1 for v in df[col].dropna()
                if pd.notna(v) and _to_minutes(v) is None
            )
            if mean_val > 60 and spread > 120 and non_time_str == 0:
                station_cols.append(col)
            else:
                meta_cols.append(col)

        # Step 3: 상행/하행 분리
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

        # Step 4: ID 컬럼 탐색
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
                    elif id_col_rev is None and str(col) != str(id_col):
                        id_col_rev = col
                        break
            except Exception:
                continue

        # Step 5: trip 추출
        trips = []
        for idx, row in df.iterrows():
            # Forward
            if fwd:
                tid = row.get(id_col) if id_col else idx * 2 + 1
                if pd.notna(tid):
                    times = []
                    for st in fwd:
                        val = row.get(st)
                        if pd.notna(val):
                            m = _to_minutes(val)
                            if m is not None:
                                times.append((str(st), m))
                    # 자정 넘김 보정: 역 시퀀스에서 시각이 감소하면 +1440
                    times = _fix_midnight_wrap(times)
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid) if not isinstance(tid, str) else tid,
                            "direction": "forward",
                            "dep_station": times[0][0],
                            "arr_station": times[-1][0],
                            "trip_dep_time": times[0][1],
                            "trip_arr_time": times[-1][1],
                            "trip_duration": times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })
            # Reverse
            if has_round and rev:
                tid_r = row.get(id_col_rev) if id_col_rev else (
                    int(row.get(id_col, 0)) + 1
                    if id_col and pd.notna(row.get(id_col))
                    else idx * 2 + 2
                )
                if pd.notna(tid_r):
                    times = []
                    for st in rev:
                        val = row.get(st)
                        if pd.notna(val):
                            m = _to_minutes(val)
                            if m is not None:
                                times.append((str(st).replace(".1", "").strip(), m))
                    # 자정 넘김 보정: 역 시퀀스에서 시각이 감소하면 +1440
                    times = _fix_midnight_wrap(times)
                    if len(times) >= 2:
                        trips.append({
                            "trip_id": int(tid_r) if not isinstance(tid_r, str) else tid_r,
                            "direction": "reverse",
                            "dep_station": times[0][0],
                            "arr_station": times[-1][0],
                            "trip_dep_time": times[0][1],
                            "trip_arr_time": times[-1][1],
                            "trip_duration": times[-1][1] - times[0][1],
                            "station_count": len(times),
                        })

        if not trips:
            return None

        result = pd.DataFrame(trips)
        result = result.sort_values("trip_dep_time").reset_index(drop=True)
        logger.info(
            f"PivotUnpivoter: {len(result)} trips "
            f"(fwd={len(result[result.direction=='forward'])}, "
            f"rev={len(result[result.direction=='reverse'])})"
        )
        return result


class ParameterExtractor:
    """텍스트 파일 또는 소규모 테이블에서 key-value 파라미터 추출."""

    @staticmethod
    def from_text(file_path: Path) -> List[dict]:
        text = _read_text_safe(file_path)
        if not text:
            return []

        rows = []
        param_idx = 0

        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            extracted = []

            # N시간 M분
            for m in re.finditer(
                r"(\d+)\s*(?:시간|hours?|hrs?)\s*(?:(\d+)\s*(?:분|minutes?|mins?))?",
                line, re.IGNORECASE,
            ):
                hrs = int(m.group(1))
                mins = int(m.group(2)) if m.group(2) else 0
                extracted.append(("duration", hrs * 60 + mins, "minutes", m.start(), m.end()))

            # N분
            for m in re.finditer(r"(\d+)\s*(?:분|minutes?|mins?)(?!\w)", line, re.IGNORECASE):
                val = int(m.group(1))
                pos = m.start()
                if not any(pos >= e[3] and pos <= e[4] for e in extracted):
                    extracted.append(("duration", val, "minutes", pos, m.end()))

            # HH:MM
            for m in re.finditer(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", line):
                h, mi = int(m.group(1)), int(m.group(2))
                if 0 <= h <= 23 and 0 <= mi <= 59:
                    extracted.append(("time_of_day", h * 60 + mi, "minutes_from_midnight", m.start(), m.end()))

            # N개
            for m in re.finditer(
                r"(\d+)\s*(?:개|ea|units?|trips?|duties?|services?)(?:\s|$|[^\w])",
                line, re.IGNORECASE,
            ):
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

        logger.info(f"ParameterExtractor.from_text: {len(rows)} params from {file_path.name}")
        return rows

    @staticmethod
    def from_small_table(df: pd.DataFrame, source_name: str) -> List[dict]:
        rows = []
        if df is None or df.empty or len(df.columns) < 2:
            return rows

        # key-value 패턴: 첫 컬럼이 텍스트, 둘째 컬럼이 값
        text_col = None
        val_col = None
        for col in df.columns:
            sample = df[col].dropna()
            if len(sample) == 0:
                continue
            numeric_ratio = sum(1 for v in sample if _to_minutes(v) is not None or isinstance(v, (int, float))) / len(sample)
            if numeric_ratio < 0.3 and text_col is None:
                text_col = col
            elif numeric_ratio >= 0.5 and val_col is None:
                val_col = col

        if text_col is not None and val_col is not None:
            # text_col, val_col 외 나머지 텍스트 컬럼을 context로 사용
            _other_text_cols = [c for c in df.columns if c != text_col and c != val_col]
            for _, row in df.iterrows():
                key = str(row.get(text_col, "")).strip()
                val = row.get(val_col)
                if key and pd.notna(val):
                    minutes = _to_minutes(val)
                    # context: 나머지 텍스트 컬럼 값을 결합
                    ctx_parts = []
                    for oc in _other_text_cols:
                        ov = row.get(oc)
                        if pd.notna(ov):
                            ctx_parts.append(str(ov).strip())
                    ctx = " ".join(ctx_parts)
                    rows.append({
                        "param_name": key,
                        "value": minutes if minutes is not None else val,
                        "unit": "minutes" if minutes is not None else "raw",
                        "source": source_name,
                        "context": ctx,
                    })
        else:
            # 크로스탭 형태: 각 셀을 파라미터로
            for _, row in df.iterrows():
                for col in df.columns:
                    val = row.get(col)
                    if pd.notna(val):
                        minutes = _to_minutes(val)
                        if minutes is not None:
                            rows.append({
                                "param_name": str(col),
                                "value": minutes,
                                "unit": "minutes",
                                "source": source_name,
                            })

        logger.info(f"ParameterExtractor.from_small_table: {len(rows)} params from {source_name}")
        return rows


# ════════════════════════════════════════
# 메인 스킬 클래스
# ════════════════════════════════════════



class ConstraintSemanticMapper:
    """constraints.yaml의 detection_hints를 기반으로
    Phase 1 추출 파라미터의 context → 영문 param_id 매핑"""

    def __init__(self):
        self._rules = []  # [(param_id, hints, operator_hint, typical_range, context_must, context_exclude)]  # ★ CHANGED
        self._load_rules()

    def _load_rules(self):
        """constraints.yaml에서 매핑 규칙 로드"""
        import os
        try:
            import yaml
        except ImportError:
            return
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        domains_dir = os.path.join(base, "knowledge", "domains")
        if not os.path.isdir(domains_dir):
            return
        for dname in os.listdir(domains_dir):
            cpath = os.path.join(domains_dir, dname, "constraints.yaml")
            if not os.path.isfile(cpath):
                continue
            try:
                with open(cpath, "r", encoding="utf-8") as f:
                    cdata = yaml.safe_load(f) or {}
            except Exception:
                continue
            for section in ["hard", "soft"]:
                for cid, cdef in (cdata.get(section) or {}).items():
                    if not isinstance(cdef, dict):
                        continue

                    # ★ CHANGED: detection_hints를 list와 dict 모두 지원
                    raw_hints = cdef.get("detection_hints") or []
                    if isinstance(raw_hints, list):
                        hints = raw_hints
                    elif isinstance(raw_hints, dict):
                        # 기존 {"ko": [...]} 형태도 호환
                        hints = raw_hints.get("ko", [])
                    else:
                        hints = []

                    if not hints:
                        continue

                    # ★ NEW: context_must / context_exclude 로딩
                    context_must = cdef.get("context_must", [])
                    context_exclude = cdef.get("context_exclude", [])

                    # single_param
                    param_id = cdef.get("parameter")
                    typical = cdef.get("typical_range", [])
                    if param_id:
                        self._rules.append((param_id, hints, "<=", typical, context_must, context_exclude))

                    # ★ CHANGED: compound params - list 형태도 지원
                    raw_params = cdef.get("parameters") or {}
                    if isinstance(raw_params, list):
                        # list of param names (e.g., day_night_classification)
                        for pid in raw_params:
                            if isinstance(pid, str):
                                self._rules.append((pid, hints, "<=", typical, context_must, context_exclude))
                    elif isinstance(raw_params, dict):
                        for pid, pdef in raw_params.items():
                            if not isinstance(pdef, dict):
                                # pid만 있고 정의가 없는 경우
                                self._rules.append((pid, hints, "<=", typical, context_must, context_exclude))
                                continue
                            ptypical = pdef.get("typical_range", [])
                            sub_hints_raw = pdef.get("detection_hints") or []
                            if isinstance(sub_hints_raw, list):
                                sub_hints = sub_hints_raw
                            elif isinstance(sub_hints_raw, dict):
                                sub_hints = sub_hints_raw.get("ko", [])
                            else:
                                sub_hints = []
                            effective_hints = sub_hints if sub_hints else hints
                            sub_ctx_must = pdef.get("context_must", context_must)
                            sub_ctx_exclude = pdef.get("context_exclude", context_exclude)
                            self._rules.append((pid, effective_hints, "<=", ptypical, sub_ctx_must, sub_ctx_exclude))

                    # conditional trigger/consequence
                    trigger = cdef.get("trigger") or {}
                    if trigger.get("threshold_param"):
                        self._rules.append((
                            trigger["threshold_param"], hints, ">",
                            trigger.get("typical_threshold_range", []),
                            context_must, context_exclude
                        ))
                    consequence = cdef.get("consequence") or {}
                    if consequence.get("parameter"):
                        self._rules.append((
                            consequence["parameter"], hints, ">=",
                            consequence.get("typical_range", []),
                            context_must, context_exclude
                        ))

        # ── v3 format: "constraints" unified key ──
        for dname2 in os.listdir(domains_dir):
            cpath2 = os.path.join(domains_dir, dname2, "constraints.yaml")
            if not os.path.isfile(cpath2):
                continue
            try:
                with open(cpath2, "r", encoding="utf-8") as f2:
                    cdata2 = yaml.safe_load(f2) or {}
            except Exception:
                continue
            if "constraints" not in cdata2:
                continue
            # Skip if already processed via hard/soft
            if cdata2.get("hard") or cdata2.get("soft"):
                continue
            for cid, cdef in cdata2["constraints"].items():
                if not isinstance(cdef, dict):
                    continue
                raw_hints = cdef.get("detection_hints") or []
                if isinstance(raw_hints, list):
                    hints = raw_hints
                elif isinstance(raw_hints, dict):
                    hints = raw_hints.get("ko", [])
                else:
                    hints = []
                if not hints:
                    continue
                context_must = cdef.get("context_must", [])
                context_exclude = cdef.get("context_exclude", [])
                category = cdef.get("category", "hard")
                # single param
                param_id = cdef.get("parameter")
                typical = cdef.get("typical_range", [])
                if param_id:
                    self._rules.append((param_id, hints, "<=", typical, context_must, context_exclude))
                # parameters list or dict
                raw_params = cdef.get("parameters") or {}
                if isinstance(raw_params, list):
                    for pid in raw_params:
                        if isinstance(pid, str):
                            self._rules.append((pid, hints, "<=", typical, context_must, context_exclude))
                elif isinstance(raw_params, dict):
                    for pid, pdef in raw_params.items():
                        if not isinstance(pdef, dict):
                            self._rules.append((pid, hints, "<=", typical, context_must, context_exclude))
                            continue
                        ptypical = pdef.get("typical_range", [])
                        sub_hints_raw = pdef.get("detection_hints") or []
                        if isinstance(sub_hints_raw, list):
                            sub_hints = sub_hints_raw
                        elif isinstance(sub_hints_raw, dict):
                            sub_hints = sub_hints_raw.get("ko", [])
                        else:
                            sub_hints = []
                        effective_hints = sub_hints if sub_hints else hints
                        sub_ctx_must = pdef.get("context_must", context_must)
                        sub_ctx_exclude = pdef.get("context_exclude", context_exclude)
                        self._rules.append((pid, effective_hints, "<=", ptypical, sub_ctx_must, sub_ctx_exclude))

                logger.info(f"ConstraintSemanticMapper: {len(self._rules)} rules loaded")

    def map_param(self, param_name: str, context: str, value, unit: str = "") -> str:
        """context와 value를 분석하여 가장 적합한 영문 param_id 반환.
        매칭 실패 시 원래 param_name 반환."""
        if not self._rules:
            return param_name

        text = f"{param_name} {context}".lower()
        best_id = None
        best_score = 0

        # Parameter Catalog 로드 (type/valid_range 기반 필터링)
        if not hasattr(self, '_catalog'):
            try:
                from engine.policy.parameter_catalog import ParameterCatalog
                self._catalog = ParameterCatalog("railway")
            except Exception:
                self._catalog = None

        for rule in self._rules:
            param_id, hints, op_hint, typical_range = rule[0], rule[1], rule[2], rule[3]
            context_must = rule[4] if len(rule) > 4 else []
            context_exclude = rule[5] if len(rule) > 5 else []

            # ── Catalog type 체크: boolean param에 numeric value 매칭 방지 ──
            if self._catalog and self._catalog.has_catalog():
                entry = self._catalog.get_entry(param_id)
                if entry:
                    # boolean param에 numeric value 매칭 금지
                    if entry.type == "boolean":
                        try:
                            float(value)
                            continue  # numeric value → boolean param 스킵
                        except (ValueError, TypeError):
                            pass
                    # valid_range 밖이면 강한 감점 (typical_range보다 catalog 우선)
                    if entry.valid_range and len(entry.valid_range) >= 2:
                        try:
                            fval = float(value)
                            lo, hi = float(entry.valid_range[0]), float(entry.valid_range[1])
                            if fval < lo * 0.5 or fval > hi * 2.0:
                                continue  # catalog range에서 크게 벗어나면 후보 제외
                        except (ValueError, TypeError):
                            pass

            # context_exclude 체크 – 제외 키워드가 있으면 스킵
            if context_exclude:
                excluded = False
                for ex_kw in context_exclude:
                    if ex_kw.lower() in text:
                        excluded = True
                        break
                if excluded:
                    continue

            score = 0
            for hint in hints:
                if hint.lower() in text:
                    score += len(hint) * 2  # 긴 힌트일수록 높은 점수

            if score == 0:
                continue

            # context_must: 필수 키워드 중 하나라도 있어야 후보로 인정
            if context_must:
                must_found = any(kw.lower() in text for kw in context_must)
                if not must_found:
                    continue  # 필수 키워드 없으면 이 규칙 스킵
                score += 10  # 필수 키워드 매칭 보너스

            # typical_range 내에 있으면 큰 보너스 (동일 context 구분 핵심)
            if typical_range and len(typical_range) == 2:
                try:
                    fval = float(value)
                    if typical_range[0] <= fval <= typical_range[1]:
                        score += 20  # 정확히 범위 안 -> 큰 보너스
                    elif typical_range[0] * 0.5 <= fval <= typical_range[1] * 1.5:
                        score += 5   # 근접 범위
                    else:
                        score -= 10  # 범위 밖이면 감점
                except (ValueError, TypeError):
                    pass

            if score > best_score:
                best_score = score
                best_id = param_id

        return best_id if best_id and best_score >= 4 else param_name


_semantic_mapper = None
def _get_semantic_mapper():
    global _semantic_mapper
    if _semantic_mapper is None:
        _semantic_mapper = ConstraintSemanticMapper()
    return _semantic_mapper

class StructuralNormalizationSkill:
    """
    Phase 1: 구조 정규화.
    LLM 불필요. 문제 정의 이전에 실행.
    """

    async def handle(
        self, session: CrewSession, project_id: str,
        message: str, params: Dict
    ) -> Dict:
        state = session.state

        # 이미 완료된 경우
        if state.structural_normalization_done and not any(
            kw in message for kw in ["다시", "재", "reset", "re"]
        ):
            summary = state.phase1_summary or {}
            return {
                "type": "structural_normalization",
                "text": self._format_summary(summary),
                "data": {
                    "view_mode": "phase1_complete",
                    "phase1_summary": summary,
                    "agent_status": "structural_normalization_done",
                    "auto_next": "problem_definition",
                },
                "options": [
                    {"label": "문제 정의 진행", "action": "send", "message": "문제 정의 시작"},
                    {"label": "구조 정규화 재실행", "action": "send", "message": "구조 정규화 다시"},
                ],
            }

        # 실행
        upload_dir = _get_safe_dir(project_id)
        if not upload_dir.exists():
            return {
                "type": "error",
                "text": "업로드된 파일이 없습니다.",
                "data": None,
                "options": [{"label": "파일 업로드", "action": "upload"}],
            }

        result = await asyncio.to_thread(self._run_phase1, upload_dir)

        if result.get("error"):
            return {
                "type": "error",
                "text": f"구조 정규화 실패: {result['error']}",
                "data": {"agent_status": "structural_normalization_failed"},
                "options": [{"label": "재시도", "action": "send", "message": "구조 정규화 시작"}],
            }

        # data_facts 갱신: structural_normalization에서 확정된 데이터 사실
        state.data_facts = state.data_facts or {}
        state.data_facts["trip_count"] = result.get("timetable_trips", 0)
        state.data_facts["param_count"] = result.get("parameters_extracted", 0)
        state.data_facts["overlap_pair_count"] = result.get("overlap_pairs", 0)
        state.data_facts["sequential_pair_count"] = result.get("sequential_pairs", 0)
        trip_stats = result.get("trip_stats", {})
        state.data_facts["station_count"] = len(trip_stats.get("stations", []))

        # 세션 저장
        state.structural_normalization_done = True
        state.phase1_summary = result
        save_session_state(project_id, state)

        return {
            "type": "structural_normalization",
            "text": self._format_summary(result),
            "data": {
                "view_mode": "phase1_complete",
                "phase1_summary": result,
                "agent_status": "structural_normalization_done",
                "auto_next": "problem_definition",
            },
            "options": [
                {"label": "문제 정의 진행", "action": "send", "message": "문제 정의 시작"},
                {"label": "구조 정규화 재실행", "action": "send", "message": "구조 정규화 다시"},
            ],
        }

    # ──────────────────────────────────
    # Phase 1 실행 (동기, 스레드에서 실행)
    # ──────────────────────────────────
    def _run_phase1(self, upload_dir: Path) -> dict:
        phase1_dir = upload_dir / "phase1"
        phase1_dir.mkdir(parents=True, exist_ok=True)

        report = {
            "files_processed": [],
            "timetable_trips": 0,
            "parameters_extracted": 0,
            "structure_types": {},
            "warnings": [],
        }

        all_trips = []
        all_params = []

        for fp in sorted(upload_dir.iterdir()):
            if fp.is_dir() or fp.name.startswith("."):
                continue

            ext = fp.suffix.lower()
            file_entry = {"name": fp.name, "type": ext, "sheets": []}

            try:
                if ext in (".xlsx", ".xls"):
                    file_entry.update(self._process_excel(fp, all_trips, all_params))
                elif ext == ".csv":
                    file_entry.update(self._process_csv(fp, all_trips, all_params))
                elif ext == ".tsv":
                    file_entry.update(self._process_csv(fp, all_trips, all_params, sep="\t"))
                elif ext == ".json":
                    file_entry.update(self._process_json(fp, all_params))
                elif ext == ".ods":
                    file_entry.update(self._process_excel(fp, all_trips, all_params))
                elif ext in (".hwp", ".hwpx", ".doc"):
                    file_entry["structure"] = "document_skipped"
                    file_entry["note"] = "문서 파일은 Phase 1에서 구조 변환 불가. 문제 정의에서 LLM으로 처리."
                elif ext in (".txt", ".md", ".text"):
                    file_entry.update(self._process_text(fp, all_params))
                elif ext == ".pdf":
                    file_entry["structure"] = "pdf_skipped"
                    file_entry["note"] = "PDF는 Phase 1에서 구조 변환 불가. 문제 정의에서 LLM으로 처리."
                elif ext == ".docx":
                    file_entry["structure"] = "docx_skipped"
                    file_entry["note"] = "DOCX는 Phase 1에서 구조 변환 불가. 문제 정의에서 LLM으로 처리."
                else:
                    file_entry["structure"] = "unsupported"
                    continue

            except Exception as e:
                logger.error(f"Phase1 error [{fp.name}]: {e}", exc_info=True)
                file_entry["error"] = str(e)
                report["warnings"].append(f"{fp.name}: {e}")

            report["files_processed"].append(file_entry)

        # trips 저장
        if all_trips:
            trips_df = pd.DataFrame(all_trips)
            trips_df = trips_df.sort_values("trip_dep_time").reset_index(drop=True)
            trips_df.to_csv(str(phase1_dir / "timetable_rows.csv"), index=False, encoding="utf-8")
            report["timetable_trips"] = len(trips_df)

            # ── 시간 겹침 쌍 사전 계산 (time_compatibility용) ──
            if "trip_dep_time" in trips_df.columns and "trip_arr_time" in trips_df.columns:
                try:
                    dep = trips_df["trip_dep_time"].astype(float).values
                    arr = trips_df["trip_arr_time"].astype(float).values
                    ids = trips_df["trip_id"].astype(str).values
                    overlap_pairs = []
                    n = len(dep)
                    for i in range(n):
                        for j in range(i + 1, n):
                            if dep[i] < arr[j] and dep[j] < arr[i]:
                                overlap_pairs.append([ids[i], ids[j]])
                    overlap_path = phase1_dir / "overlap_pairs.json"
                    with open(str(overlap_path), "w", encoding="utf-8") as _of:
                        json.dump(overlap_pairs, _of)
                    report["overlap_pairs"] = len(overlap_pairs)
                    logger.info(
                        f"Overlap pairs: {len(overlap_pairs)} / {n*(n-1)//2} total pairs "
                        f"({len(overlap_pairs)*100//(n*(n-1)//2) if n > 1 else 0}%)"
                    )
                except Exception as e:
                    logger.warning(f"Overlap pairs calculation failed: {e}")

                # ★ NEW: 연속 가능 쌍 사전 계산 (max_single_wait_time용)
                try:
                    dep = trips_df["trip_dep_time"].astype(float).values
                    arr = trips_df["trip_arr_time"].astype(float).values
                    ids = trips_df["trip_id"].astype(str).values

                    # 역 정보가 있으면 같은 역 조건 적용
                    has_stations = (
                        "arr_station" in trips_df.columns
                        and "dep_station" in trips_df.columns
                    )
                    if has_stations:
                        arr_st = trips_df["arr_station"].astype(str).values
                        dep_st = trips_df["dep_station"].astype(str).values

                    sequential_pairs = []
                    n = len(dep)

                    # reference_ranges.yaml에서 도메인 기준값 로딩
                    MIN_GAP = 5      # fallback
                    MAX_GAP = 720    # fallback
                    try:
                        import yaml as _yaml
                        _ref_dir = Path(__file__).resolve().parents[3] / "knowledge" / "domains"
                        for _dname in _ref_dir.iterdir():
                            _rpath = _dname / "reference_ranges.yaml"
                            if _rpath.is_file():
                                with open(str(_rpath), "r", encoding="utf-8") as _rf:
                                    _rdata = _yaml.safe_load(_rf) or {}
                                for _region in _rdata.values():
                                    if isinstance(_region, dict) and "values" in _region:
                                        _vals = _region["values"]
                                        MIN_GAP = _vals.get("min_wait_minutes", MIN_GAP)
                                        MAX_GAP = _vals.get("max_total_stay_minutes", MAX_GAP)
                                        break
                                break
                    except Exception as _e:
                        logger.debug(f"Reference ranges load skipped: {_e}")

                    for i in range(n):
                        for j in range(n):
                            if i == j:
                                continue
                            gap = dep[j] - arr[i]
                            if gap < MIN_GAP or gap > MAX_GAP:
                                continue
                            # 역 조건: i의 도착역 == j의 출발역
                            if has_stations and arr_st[i] != dep_st[j]:
                                continue
                            sequential_pairs.append([ids[i], ids[j]])

                    seq_path = phase1_dir / "sequential_pairs.json"
                    with open(str(seq_path), "w", encoding="utf-8") as _sf:
                        json.dump(sequential_pairs, _sf)
                    report["sequential_pairs"] = len(sequential_pairs)
                    logger.info(
                        f"Sequential pairs: {len(sequential_pairs)} "
                        f"(station_filter={'ON' if has_stations else 'OFF'})"
                    )
                except Exception as e:
                    logger.warning(f"Sequential pairs calculation failed: {e}")
                    report["warnings"].append(f"sequential_pairs 생성 실패: {e}")

            # 기본 통계
            report["trip_stats"] = {
                "total": len(trips_df),
                "forward": int((trips_df.get("direction") == "forward").sum()) if "direction" in trips_df.columns else 0,
                "reverse": int((trips_df.get("direction") == "reverse").sum()) if "direction" in trips_df.columns else 0,
                "first_departure": float(trips_df["trip_dep_time"].min()),
                "last_arrival": float(trips_df["trip_arr_time"].max()),
                "avg_duration": round(float(trips_df["trip_duration"].mean()), 1),
                "stations": sorted(
                    set(trips_df["dep_station"].tolist() + trips_df["arr_station"].tolist())
                ) if "dep_station" in trips_df.columns else [],
            }

        # params 저장
        # ── Semantic mapping: assign meaningful English param IDs ──
        mapper = _get_semantic_mapper()
        _used_ids = {}  # track assigned IDs to handle duplicates
        _used_values = {}  # track (semantic_id, value) to skip exact duplicates
        for p in all_params:
            original_name = p.get("param_name", "")
            ctx = p.get("context", "")
            val = p.get("value", "")
            unit = p.get("unit", "")
            mapped_id = mapper.map_param(original_name, ctx, val, unit)
            if mapped_id != original_name:
                # 동일 semantic_id + 동일 value면 중복 -> 스킵
                dup_key = (mapped_id, str(val))
                if dup_key in _used_values:
                    logger.info(f"Skipped duplicate: {mapped_id}={val} (same as existing)")
                    p["_skip"] = True
                    p["semantic_id"] = mapped_id
                    continue
                # Handle duplicates: append _max, _min, _avg based on context
                if mapped_id in _used_ids:
                    ctx_lower = ctx.lower()
                    if any(k in ctx_lower for k in ["평균", "average", "avg"]):
                        mapped_id = f"{mapped_id}_avg"
                    elif any(k in ctx_lower for k in ["최소", "min", "이상"]):
                        mapped_id = f"{mapped_id}_min"
                    elif any(k in ctx_lower for k in ["최대", "max", "이내", "이하"]):
                        mapped_id = f"{mapped_id}_max"
                    else:
                        _used_ids[mapped_id] = _used_ids.get(mapped_id, 0) + 1
                        mapped_id = f"{mapped_id}_{_used_ids[mapped_id]}"
                _used_ids[mapped_id] = _used_ids.get(mapped_id, 0) + 1
                _used_values[(mapped_id, str(val))] = True
                p["semantic_id"] = mapped_id
                logger.info(f"Semantic map: {original_name} -> {mapped_id} (ctx: {ctx[:50]})")
            else:
                p["semantic_id"] = original_name

        # 중복 스킵된 행 제거
        all_params = [p for p in all_params if not p.get("_skip", False)]

        if all_params:
            # ── 통계/인원 데이터 필터링 ──
            # param_type이 없고 context도 없는 행 중, param_name이
            # 제약조건 관련 키워드가 아니면 통계 데이터로 간주하여 제외
            _constraint_keywords = [
                "시간", "time", "분", "min", "준비", "정리", "대기",
                "휴식", "수면", "취침", "출고", "퇴근", "교육",
                "주간", "야간", "인원", "사업", "근무", "crew", "shift",
            ]
            filtered_params = []
            for p in all_params:
                ptype = p.get("param_type", "")
                ctx = p.get("context", "")
                pname = p.get("param_name", "")
                # param_type이 있거나 context가 있으면 유효 파라미터
                if ptype or ctx:
                    filtered_params.append(p)
                    continue
                # 시맨틱 매핑에 성공한 파라미터는 유지 (원래 이름과 다르면 매핑된 것)
                sid = p.get("semantic_id", "")
                if sid and sid != pname:
                    filtered_params.append(p)
                    continue
                # param_type/context 없어도 이름에 제약조건 키워드 있으면 유지
                # 단, 값이 HH:MM 형식이거나 "회/명/개" 단위면 통계 데이터
                _val_str = str(p.get("value", ""))
                _is_stats_value = (
                    ":" in _val_str  # HH:MM 형식
                    or _val_str.endswith("회") or _val_str.endswith("명") or _val_str.endswith("개")
                )
                # 이름이 "총 ", "월 ", "연간", "D(", "S(" 로 시작하면 통계
                _is_stats_name = (
                    pname.startswith("총 ") or pname.startswith("월 ") or
                    pname.startswith("연간") or pname.startswith("D(") or
                    pname.startswith("S(")
                )
                if _is_stats_value or _is_stats_name:
                    logger.info(f"Filtered out stats param (keyword match but stats): {pname}={_val_str}")
                    continue
                if any(kw in pname for kw in _constraint_keywords):
                    filtered_params.append(p)
                    continue
                # 그 외는 통계 데이터 -> 제외
                logger.info(f"Filtered out stats param: {pname}={p.get('value','')} (source={p.get('source','')})")
            logger.info(f"Parameter filter: {len(all_params)} -> {len(filtered_params)} (removed {len(all_params)-len(filtered_params)} stats)")
            all_params = filtered_params

            params_df = pd.DataFrame(all_params)
            params_df.to_csv(str(phase1_dir / "parameters_raw.csv"), index=False, encoding="utf-8")
            report["parameters_extracted"] = len(params_df)

        # 리포트 저장
        with open(str(phase1_dir / "structure_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

        return report

    def _process_excel(self, fp: Path, all_trips: list, all_params: list) -> dict:
        import openpyxl
        result = {"sheets": []}
        try:
            wb = openpyxl.load_workbook(str(fp), read_only=True)
            sheet_names = wb.sheetnames
            wb.close()
        except Exception:
            sheet_names = ["Sheet1"]

        for sheet in sheet_names:
            df = _read_file(fp, sheet)
            if df is None or df.empty:
                result["sheets"].append({"name": sheet, "structure": "empty"})
                continue

            structure = StructureDetector.classify(df)
            sheet_info = {"name": sheet, "structure": structure, "rows": len(df), "cols": len(df.columns)}

            if structure == "pivot_timetable":
                trips_df = PivotUnpivoter.transform(df)
                if trips_df is not None:
                    all_trips.extend(trips_df.to_dict("records"))
                    sheet_info["trips_extracted"] = len(trips_df)

            elif structure == "small_kv":
                params = ParameterExtractor.from_small_table(df, f"{fp.name}:{sheet}")
                all_params.extend(params)
                sheet_info["params_extracted"] = len(params)

            elif structure == "tabular_regular":
                sheet_info["note"] = "정규형 테이블. Phase 2에서 의미 매핑 필요."

            result["sheets"].append(sheet_info)

        return result

    def _process_csv(self, fp: Path, all_trips: list, all_params: list, sep: str = ",") -> dict:
        df = _read_file(fp)
        if df is None or df.empty:
            return {"structure": "empty"}

        structure = StructureDetector.classify(df)
        result = {"structure": structure, "rows": len(df), "cols": len(df.columns)}

        if structure == "pivot_timetable":
            trips_df = PivotUnpivoter.transform(df)
            if trips_df is not None:
                all_trips.extend(trips_df.to_dict("records"))
                result["trips_extracted"] = len(trips_df)

        elif structure == "small_kv":
            params = ParameterExtractor.from_small_table(df, fp.name)
            all_params.extend(params)
            result["params_extracted"] = len(params)

        return result

    def _process_json(self, fp: Path, all_params: list) -> dict:
        """JSON 파일에서 파라미터 추출"""
        try:
            text = _read_text_safe(fp)
            if not text:
                return {"structure": "json_empty"}
            data = __import__("json").loads(text)
            if isinstance(data, dict):
                params = []
                for key, val in data.items():
                    if isinstance(val, (int, float)):
                        params.append({
                            "param_name": str(key),
                            "value": val,
                            "unit": "raw",
                            "source": fp.name,
                        })
                all_params.extend(params)
                return {"structure": "json_kv", "params_extracted": len(params)}
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                return {"structure": "json_array", "rows": len(data), "keys": list(data[0].keys())[:10]}
            return {"structure": "json_other"}
        except Exception as e:
            return {"structure": "json_error", "error": str(e)}

    def _process_text(self, fp: Path, all_params: list) -> dict:
        params = ParameterExtractor.from_text(fp)
        all_params.extend(params)
        return {"structure": "text", "params_extracted": len(params)}

    # ──────────────────────────────────
    # 결과 포맷팅
    # ──────────────────────────────────
    def _format_summary(self, report: dict) -> str:
        lines = ["## 구조 정규화 완료 (Phase 1)\n"]

        trip_stats = report.get("trip_stats", {})
        if trip_stats:
            total = trip_stats.get("total", 0)
            fwd = trip_stats.get("forward", 0)
            rev = trip_stats.get("reverse", 0)
            first = trip_stats.get("first_departure", 0)
            last = trip_stats.get("last_arrival", 0)
            avg = trip_stats.get("avg_duration", 0)
            stations = trip_stats.get("stations", [])

            first_h, first_m = int(first // 60), int(first % 60)
            last_h, last_m = int(last // 60), int(last % 60)

            lines.append(f"**시간표 변환**: {total}개 운행 추출")
            lines.append(f"  - 상행: {fwd}개, 하행: {rev}개")
            lines.append(f"  - 첫 출발: {first_h:02d}:{first_m:02d}, 마지막 도착: {last_h:02d}:{last_m:02d}")
            lines.append(f"  - 평균 운행시간: {avg}분")
            if stations:
                lines.append(f"  - 역 목록: {', '.join(stations[:10])}")
                if len(stations) > 10:
                    lines.append(f"    ... 외 {len(stations)-10}개")
            lines.append("")

        params_count = report.get("parameters_extracted", 0)
        if params_count:
            lines.append(f"**파라미터 추출**: {params_count}개 항목")
            lines.append("")

        # ★ NEW: overlap/sequential pairs 통계 표시
        overlap_count = report.get("overlap_pairs", 0)
        seq_count = report.get("sequential_pairs", 0)
        if overlap_count or seq_count:
            lines.append("**사전 계산:**")
            if overlap_count:
                lines.append(f"  - 시간 겹침 쌍 (overlap): {overlap_count}개")
            if seq_count:
                lines.append(f"  - 연속 가능 쌍 (sequential): {seq_count}개")
            lines.append("")

        # 파일별 처리 결과
        files = report.get("files_processed", [])
        if files:
            lines.append("**파일별 처리 결과:**")
            for fe in files:
                name = fe.get("name", "?")
                struct = fe.get("structure", "unknown")
                sheets = fe.get("sheets", [])
                if sheets:
                    for sh in sheets:
                        lines.append(f"  - {name} [{sh.get('name','')}]: {sh.get('structure','')} ({sh.get('rows',0)} rows)")
                else:
                    lines.append(f"  - {name}: {struct}")
            lines.append("")

        warnings = report.get("warnings", [])
        if warnings:
            lines.append("**경고:**")
            for w in warnings:
                lines.append(f"  - {w}")
            lines.append("")

        lines.append("다음 단계: **문제 정의**로 진행하시겠습니까?")
        return "\n".join(lines)


# ── 모듈 레벨 함수 ──
_skill_instance: Optional[StructuralNormalizationSkill] = None


def get_skill() -> StructuralNormalizationSkill:
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = StructuralNormalizationSkill()
    return _skill_instance


async def skill_structural_normalization(
    session: CrewSession, project_id: str,
    message: str, params: Dict
) -> Dict:
    skill = get_skill()
    return await skill.handle(session, project_id, message, params)
