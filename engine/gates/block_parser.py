"""
engine/gates/block_parser.py
────────────────────────────
범용 비정형 블록 파서

Gate 1에서 감지한 "non_tabular_block" 구조의 시트를 자동으로 파싱하여
블록 단위 요약 테이블을 생성한다.

범용 설계 원칙:
  1. 블록 구분: 빈 행(all NaN)으로 분리
  2. 블록 ID: 블록 첫 행에서 추출 (나머지 셀이 비어있으면 ID 행으로 판단)
  3. 헤더: 블록 내 첫 데이터 행이 문자열만 포함하면 헤더로 사용
  4. 데이터: 헤더 이후 행을 DataFrame으로 변환
  5. 메타 행: 특정 패턴(예: "휴식:", "합계:" 등)은 메타데이터로 분리
  6. 요약: 블록별로 수치 컬럼 합산, 시간 컬럼 변환 등
"""

import logging
import re
import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# 메타 행 감지 패턴 (첫 번째 비빈 셀에서 매칭)
META_PATTERNS = [
    re.compile(r"^(휴식|rest|break)\s*[:：]", re.IGNORECASE),
    re.compile(r"^(합계|total|sum)\s*[:：]?", re.IGNORECASE),
    re.compile(r"^(소계|subtotal)\s*[:：]?", re.IGNORECASE),
]


def parse_blocks(df: pd.DataFrame) -> Dict[str, Any]:
    """
    비정형 블록 구조의 DataFrame을 파싱.

    Args:
        df: header=None으로 읽은 원시 DataFrame

    Returns:
        {
            "blocks": [
                {
                    "block_id": "DIA 0",
                    "header": ["출발역", "출발시간", ...],
                    "data": DataFrame,       # 정형화된 데이터
                    "meta": {"휴식": "05:49:00", ...},
                },
                ...
            ],
            "summary_df": DataFrame,  # 블록별 요약 테이블
            "block_count": int,
        }
    """
    raw_blocks = _split_by_blank_rows(df)
    parsed_blocks = []

    for raw in raw_blocks:
        if len(raw) == 0:
            continue
        block = _parse_single_block(raw)
        if block and block.get("data") is not None and len(block["data"]) > 0:
            parsed_blocks.append(block)

    summary_df = _build_summary(parsed_blocks)

    result = {
        "blocks": parsed_blocks,
        "summary_df": summary_df,
        "block_count": len(parsed_blocks),
    }

    logger.info(f"BlockParser: {len(parsed_blocks)} blocks parsed, summary shape={summary_df.shape}")
    return result


def _split_by_blank_rows(df: pd.DataFrame) -> List[pd.DataFrame]:
    """빈 행으로 DataFrame을 블록 단위로 분리"""
    blank_mask = df.isna().all(axis=1)
    blocks = []
    current_rows = []

    for i in range(len(df)):
        if blank_mask.iloc[i]:
            if current_rows:
                blocks.append(df.iloc[current_rows].reset_index(drop=True))
                current_rows = []
        else:
            current_rows.append(i)

    if current_rows:
        blocks.append(df.iloc[current_rows].reset_index(drop=True))

    return blocks


def _parse_single_block(block_df: pd.DataFrame) -> Optional[Dict]:
    """단일 블록을 파싱"""
    if len(block_df) == 0:
        return None

    block_id = None
    header = None
    data_rows = []
    meta = {}
    start_idx = 0

    # 1. 블록 ID 추출: 첫 행에서 첫 셀만 값이 있고 나머지가 비어있으면 ID
    first_row = block_df.iloc[0]
    non_null_count = first_row.notna().sum()
    if non_null_count <= 2:
        first_val = str(first_row.dropna().iloc[0]) if first_row.notna().any() else ""
        if first_val and not _is_header_row(first_row, block_df):
            block_id = first_val.strip()
            start_idx = 1

    # 2. 헤더 추출: ID 다음 행이 모두 문자열이면 헤더
    if start_idx < len(block_df):
        candidate = block_df.iloc[start_idx]
        if _is_header_row(candidate, block_df):
            header = [str(v).strip() if pd.notna(v) else f"col_{i}"
                     for i, v in enumerate(candidate)]
            start_idx += 1

    # 3. 데이터/메타 분류
    for i in range(start_idx, len(block_df)):
        row = block_df.iloc[i]
        if _is_meta_row(row):
            meta_key, meta_val = _extract_meta(row)
            if meta_key:
                meta[meta_key] = meta_val
        else:
            data_rows.append(row.values)

    # 4. DataFrame 생성
    if not data_rows:
        return {"block_id": block_id, "header": header, "data": pd.DataFrame(), "meta": meta}

    data_df = pd.DataFrame(data_rows, columns=header if header else None)

    # 5. 시간 컬럼 자동 변환
    data_df = _convert_time_columns(data_df)

    return {
        "block_id": block_id,
        "header": header,
        "data": data_df,
        "meta": meta,
    }


def _is_header_row(row: pd.Series, block_df: pd.DataFrame) -> bool:
    """행이 헤더인지 판단"""
    non_null = row.dropna()
    if len(non_null) < 3:
        return False

    # 모든 값이 문자열이고 숫자가 아니면 헤더
    all_str = True
    for v in non_null:
        s = str(v).strip()
        if not s:
            continue
        try:
            float(s)
            all_str = False
            break
        except ValueError:
            # 시간 패턴도 데이터로 취급
            if re.match(r"^\d{1,2}:\d{2}", s):
                all_str = False
                break

    return all_str


def _is_meta_row(row: pd.Series) -> bool:
    """행이 메타데이터(휴식, 합계 등)인지 판단"""
    non_null = row.dropna()
    if len(non_null) == 0:
        return False

    # 비빈 셀이 1~2개이고 패턴에 매칭되면 메타
    if len(non_null) <= 2:
        first_val = str(non_null.iloc[0]).strip()
        for pattern in META_PATTERNS:
            if pattern.match(first_val):
                return True

    return False


def _extract_meta(row: pd.Series) -> Tuple[Optional[str], Optional[str]]:
    """메타 행에서 키-값 추출"""
    non_null = row.dropna()
    if len(non_null) == 0:
        return None, None

    text = str(non_null.iloc[0]).strip()

    # "키: 값" 패턴
    match = re.match(r"^(.+?)\s*[:：]\s*(.+)$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    return text, None


def _convert_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    """시간 형태의 컬럼을 분(minutes) 정수로 변환"""
    df = df.copy()

    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue

        # datetime.time 객체인 경우
        if isinstance(series.iloc[0], datetime.time):
            df[col + "_min"] = df[col].apply(
                lambda v: v.hour * 60 + v.minute + v.second / 60
                if isinstance(v, datetime.time) else None
            )
            continue

        # 문자열 시간 패턴 (HH:MM 또는 HH:MM:SS)
        if series.dtype == object:
            sample = series.head(10).astype(str)
            time_match_count = sample.apply(
                lambda x: bool(re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", x.strip()))
            ).sum()

            if time_match_count / len(sample) > 0.5:
                df[col + "_min"] = df[col].apply(_time_str_to_minutes)

    return df


def _time_str_to_minutes(val) -> Optional[float]:
    """시간 문자열을 분으로 변환"""
    if pd.isna(val):
        return None
    s = str(val).strip()
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        sec = int(match.group(3)) if match.group(3) else 0
        return h * 60 + m + sec / 60
    return None


def _build_summary(blocks: List[Dict]) -> pd.DataFrame:
    """블록별 요약 테이블 생성"""
    rows = []

    for block in blocks:
        bid = block.get("block_id", "unknown")
        data = block.get("data", pd.DataFrame())
        meta = block.get("meta", {})

        summary = {"block_id": bid}

        # 데이터 행 수
        summary["trip_count"] = len(data)

        # 수치 컬럼 합산
        for col in data.columns:
            if col.endswith("_min"):
                continue
            series = data[col].dropna()
            if len(series) == 0:
                continue

            # 숫자 컬럼만 합산
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            if len(numeric) > 0:
                summary[f"{col}_sum"] = round(numeric.sum(), 1)
                summary[f"{col}_mean"] = round(numeric.mean(), 1)

        # _min 컬럼 (시간 변환된 컬럼) 처리
        for col in data.columns:
            if col.endswith("_min"):
                numeric = data[col].dropna()
                if len(numeric) > 0:
                    vals = sorted(numeric.tolist())
                    if len(vals) >= 2:
                        # 총 운행 시간 = 마지막 도착 - 첫 출발
                        summary[f"{col}_first"] = round(vals[0], 1)
                        summary[f"{col}_last"] = round(vals[-1], 1)
                        summary[f"{col}_span"] = round(vals[-1] - vals[0], 1)

        # 메타 데이터 추가
        for mk, mv in meta.items():
            # 휴식 시간 파싱
            if mv:
                rest_match = re.match(r"(\d{1,2}):(\d{2}):?(\d{2})?", mv)
                if rest_match:
                    h = int(rest_match.group(1))
                    m = int(rest_match.group(2))
                    s = int(rest_match.group(3)) if rest_match.group(3) else 0
                    summary[f"{mk}_min"] = round(h * 60 + m + s / 60, 1)
                else:
                    summary[mk] = mv

        rows.append(summary)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def integrate_with_databinder(parse_result: Dict,
                              dataframes: Dict[str, pd.DataFrame],
                              sheet_key: str) -> Dict[str, pd.DataFrame]:
    """
    파싱 결과를 DataBinder의 dataframes에 통합.
    원본 시트는 유지하고, 요약 테이블을 새 키로 추가.
    """
    summary_df = parse_result.get("summary_df", pd.DataFrame())
    if len(summary_df) > 0:
        new_key = f"{sheet_key}__summary"
        dataframes[new_key] = summary_df
        logger.info(
            f"BlockParser: added '{new_key}' ({len(summary_df)} rows, "
            f"{len(summary_df.columns)} cols) to dataframes"
        )

    # 개별 블록도 추가 (선택적)
    for block in parse_result.get("blocks", []):
        bid = block.get("block_id", "")
        data = block.get("data", pd.DataFrame())
        if len(data) > 0 and bid:
            block_key = f"{sheet_key}__{bid}"
            dataframes[block_key] = data

    return dataframes
