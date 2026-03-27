"""
engine/gates/gate1_data_profile.py
───────────────────────────────────
Gate 1: 데이터 품질 프로파일링

업로드된 파일들의 구조적 특성을 자동 감지하여 data_profile을 생성한다.
LLM 호출 없이 pandas + 정규표현식만으로 동작하며,
어떤 형식의 파일이든 동일한 로직이 적용된다.

반환되는 data_profile은 LLM 분석 프롬프트에 포함되어
존재하지 않는 데이터를 참조하는 실수를 방지한다.
"""

import logging
import re
import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── 시간 패턴 (문자열 컬럼에서 시간 데이터 감지) ──
TIME_PATTERNS = [
    (re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$"), "time_string"),        # "20:09", "05:20:30"
    (re.compile(r"^\d+\s*(분|시간|초|min|hour|sec)"), "duration_string"),  # "40분", "3시간"
]


def run(dataframes: Dict[str, pd.DataFrame],
        file_names: Optional[List[str]] = None,
        constraints_config: Optional[Dict] = None) -> Dict[str, Any]:
    """
    메인 프로파일링 함수.

    Args:
        dataframes: {시트키: DataFrame} 딕셔너리 (DataBinder._dataframes 형태)
        file_names: 원본 파일명 목록 (옵션)
        constraints_config: constraints.yaml 내용 (옵션, Layer 2 set source 검증용)

    Returns:
        data_profile 딕셔너리
    """
    profile: Dict[str, Any] = {
        "files": {},
        "warnings": [],
        "data_quality": [],
        "summary": {
            "total_sheets": 0,
            "total_rows": 0,
            "total_columns": 0,
            "time_columns": [],
            "high_null_columns": [],
            "non_tabular_sheets": [],
        },
    }

    for sheet_key, df in dataframes.items():
        # 블록 파서가 생성한 개별 블록 테이블은 프로파일에서 제외
        # (요약 테이블 __summary만 포함)
        if "__DIA " in sheet_key or "__Block" in sheet_key:
            continue

        sheet_profile = _profile_sheet(sheet_key, df)
        profile["files"][sheet_key] = sheet_profile
        profile["summary"]["total_sheets"] += 1
        profile["summary"]["total_rows"] += sheet_profile["rows"]
        profile["summary"]["total_columns"] += sheet_profile["col_count"]

        # 경고 수집
        for col_info in sheet_profile["columns"].values():
            if col_info.get("needs_conversion"):
                profile["summary"]["time_columns"].append(
                    f"{sheet_key}::{col_info['name']}"
                )
            if col_info.get("null_ratio", 0) > 0.5:
                profile["summary"]["high_null_columns"].append(
                    f"{sheet_key}::{col_info['name']} ({col_info['null_ratio']:.0%})"
                )

        if sheet_profile.get("structure") == "non_tabular_block":
            profile["summary"]["non_tabular_sheets"].append(sheet_key)

    # 전체 경고 메시지 생성
    profile["warnings"] = _build_warnings(profile["summary"])

    # CSV 품질 검증 (2계층: 자동 감지 + 도메인 지식)
    data_quality = _validate_csv_quality(profile["files"], constraints_config)
    profile["data_quality"] = data_quality
    if data_quality:
        for dq in data_quality:
            profile["warnings"].append(dq["message"])

    logger.info(
        f"Gate1 profile: {profile['summary']['total_sheets']} sheets, "
        f"{profile['summary']['total_rows']} rows, "
        f"{len(profile['warnings'])} warnings, "
        f"{len(data_quality)} data quality issues"
    )

    return profile


def _profile_sheet(sheet_key: str, df: pd.DataFrame) -> Dict[str, Any]:
    """개별 시트 프로파일링"""
    result: Dict[str, Any] = {
        "sheet_key": sheet_key,
        "rows": len(df),
        "col_count": len(df.columns),
        "columns": {},
        "structure": "tabular",  # 기본값
    }

    # ── 비정형 테이블 감지 ──
    if _detect_non_tabular(df):
        result["structure"] = "non_tabular_block"
        block_count = _count_blocks(df)
        result["block_count"] = block_count
        logger.info(f"  [{sheet_key}] non-tabular block structure detected ({block_count} blocks)")

    # ── 컬럼별 프로파일링 ──
    for col in df.columns:
        col_profile = _profile_column(df, col)
        result["columns"][str(col)] = col_profile

    return result


def _profile_column(df: pd.DataFrame, col: str) -> Dict[str, Any]:
    """개별 컬럼 프로파일링"""
    series = df[col]
    total = len(series)
    null_count = int(series.isna().sum())
    null_ratio = null_count / total if total > 0 else 0
    non_null = series.dropna()
    unique_count = int(non_null.nunique())

    info: Dict[str, Any] = {
        "name": str(col),
        "pandas_dtype": str(series.dtype),
        "null_count": null_count,
        "null_ratio": round(null_ratio, 3),
        "unique_count": unique_count,
        "total_count": total,
    }

    # ── 실제 타입 판별 ──
    detected_type, needs_conversion = _detect_actual_type(series, non_null)
    info["detected_type"] = detected_type
    if needs_conversion:
        info["needs_conversion"] = needs_conversion

    # ── 샘플 값 (최대 5개) ──
    if len(non_null) > 0:
        samples = non_null.head(5).tolist()
        info["sample_values"] = [str(v) for v in samples]

    # ── 카테고리 vs ID 추정 ──
    if total > 0 and unique_count > 0:
        ratio = unique_count / total
        if ratio < 0.1 and unique_count <= 20:
            info["inferred_role"] = "category"
        elif ratio > 0.9:
            info["inferred_role"] = "identifier"
        else:
            info["inferred_role"] = "measure"

    return info


def _detect_actual_type(
    series: pd.Series, non_null: pd.Series
) -> Tuple[str, Optional[str]]:
    """
    pandas dtype + 실제 값을 분석하여 세부 타입과 변환 필요 여부를 반환.

    Returns:
        (detected_type, needs_conversion)
        needs_conversion이 None이면 변환 불필요
    """
    dtype_str = str(series.dtype)

    # 1. pandas가 이미 인식한 시간 타입
    if dtype_str.startswith("datetime"):
        return ("datetime", "to_minutes")

    # 2. 실제 값이 datetime.time / timedelta인 경우
    if len(non_null) > 0:
        first_val = non_null.iloc[0]
        if isinstance(first_val, datetime.time):
            return ("datetime.time", "to_minutes")
        if isinstance(first_val, datetime.timedelta):
            return ("timedelta", "to_minutes")

    # 3. object(문자열) 컬럼 — 패턴 매칭
    if dtype_str == "object" and len(non_null) > 0:
        sample = non_null.head(20)
        str_sample = sample.astype(str)

        # 시간 패턴 체크
        for pattern, type_name in TIME_PATTERNS:
            match_count = str_sample.apply(lambda x: bool(pattern.match(x.strip()))).sum()
            if match_count / len(str_sample) > 0.5:
                return (type_name, "parse_and_convert")

        # 순수 숫자 문자열 체크 ("96", "3.14" 등)
        numeric_count = str_sample.apply(_is_numeric_string).sum()
        if numeric_count / len(str_sample) > 0.8:
            return ("numeric_string", "to_number")

        return ("string", None)

    # 4. 숫자 타입
    if "int" in dtype_str:
        return ("integer", None)
    if "float" in dtype_str:
        return ("float", None)

    return (dtype_str, None)


def _is_numeric_string(val: str) -> bool:
    """문자열이 숫자로 변환 가능한지 체크"""
    try:
        float(val.strip())
        return True
    except (ValueError, AttributeError):
        return False


def _detect_non_tabular(df: pd.DataFrame) -> bool:
    """
    비정형 블록 구조 감지.
    첫 번째 컬럼에 헤더 역할의 값이 반복적으로 나타나면 블록 구조로 판단.
    예: DIA 파일에서 '출발역'이 여러 행에 반복 출현.
    """
    if len(df) < 10 or len(df.columns) < 3:
        return False

    # 블록 파서가 생성한 요약 테이블은 정형
    # (외부에서 호출 시 sheet_key를 모르므로, 행 수가 적고 컬럼이 정리된 경우 정형 판단)
    # 이 함수는 sheet_key를 받지 않으므로 DataBinder 쪽에서 처리

    first_col = df.iloc[:, 0].astype(str)

    # 첫 번째 행의 값이 다른 행에서 반복되는지 체크
    first_row_val = str(df.iloc[0, 0]).strip()
    if not first_row_val or first_row_val == "nan":
        return False

    repeat_count = (first_col == first_row_val).sum()
    if repeat_count >= 3:
        return True

    # "DIA", "구분" 등 헤더 키워드가 반복되는지 체크
    header_keywords = ["출발역", "도착역", "DIA", "구분", "항목"]
    for kw in header_keywords:
        kw_count = first_col.str.contains(kw, na=False).sum()
        if kw_count >= 3:
            return True

    return False


def _count_blocks(df: pd.DataFrame) -> int:
    """블록 수 추정 (NaN으로 구분된 빈 행 기준)"""
    blank_rows = df.isna().all(axis=1)
    if blank_rows.sum() == 0:
        return 1

    count = 0
    in_block = False
    for is_blank in blank_rows:
        if not is_blank and not in_block:
            count += 1
            in_block = True
        elif is_blank:
            in_block = False
    return max(count, 1)


def to_text_summary(profile: Dict[str, Any]) -> str:
    """
    data_profile을 LLM 프롬프트에 포함할 텍스트로 변환.
    """
    lines = ["[데이터 프로파일]"]
    lines.append(
        f"총 {profile['summary']['total_sheets']}개 시트, "
        f"{profile['summary']['total_rows']}행, "
        f"{profile['summary']['total_columns']}개 컬럼"
    )

    if profile["warnings"]:
        lines.append("")
        lines.append("⚠ 주의사항:")
        for w in profile["warnings"]:
            lines.append(f"  - {w}")

    lines.append("")
    for sheet_key, sheet_info in profile["files"].items():
        struct = sheet_info.get("structure", "tabular")
        struct_label = "정형 테이블" if struct == "tabular" else f"비정형 블록 ({sheet_info.get('block_count', '?')}개)"
        lines.append(f"[{sheet_key}] {sheet_info['rows']}행 x {sheet_info['col_count']}열 ({struct_label})")

        for col_name, col_info in sheet_info["columns"].items():
            dtype = col_info["detected_type"]
            null_info = f", 결측 {col_info['null_ratio']:.0%}" if col_info["null_ratio"] > 0 else ""
            conv_info = f", 변환필요({col_info['needs_conversion']})" if col_info.get("needs_conversion") else ""
            role = col_info.get("inferred_role", "")
            role_info = f", 역할={role}" if role else ""
            lines.append(
                f"  - {col_name}: {dtype} (고유값 {col_info['unique_count']}{null_info}{conv_info}{role_info})"
            )

    return "\n".join(lines)


def _validate_csv_quality(
    files: Dict[str, Any],
    constraints_config: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """CSV 데이터 품질 검증 (2계층).

    Layer 1 (도메인 무관): identifier 컬럼 자동 중복 검사
    Layer 2 (도메인 인식): constraints.yaml sets → source column unique 검증

    Returns:
        데이터 품질 이슈 목록 [{layer, sheet, column, type, count, message}, ...]
    """
    issues: List[Dict[str, Any]] = []
    checked_columns: set = set()

    # ── Layer 1: identifier 컬럼 자동 중복 검사 (도메인 무관) ──
    for sheet_key, sheet_profile in files.items():
        for col_name, col_info in sheet_profile.get("columns", {}).items():
            if col_info.get("inferred_role") != "identifier":
                continue
            total = col_info.get("total_count", 0)
            unique = col_info.get("unique_count", 0)
            if total > 0 and unique < total:
                dup_count = total - unique
                issues.append({
                    "layer": "auto",
                    "sheet": sheet_key,
                    "column": col_name,
                    "type": "duplicate_id",
                    "count": dup_count,
                    "message": (
                        f"{sheet_key}:{col_name} — {dup_count}건 중복 ID 감지 "
                        f"({unique}/{total} 고유값)"
                    ),
                })
                checked_columns.add((sheet_key, col_name))

    # ── Layer 2: constraints.yaml sets 기반 unique 검증 (도메인 인식) ──
    if constraints_config:
        sets_def = constraints_config.get("sets", {})
        for set_name, set_info in sets_def.items():
            if not isinstance(set_info, dict):
                continue
            source = set_info.get("source", "")
            column = set_info.get("column", "")
            if not source or not column:
                continue

            # source 파일명 → sheet_key 매칭 (normalized/ 접두사, .csv 제거 등)
            matched_sheet = None
            source_base = source.replace(".csv", "").replace(".json", "")
            for sheet_key in files:
                # sheet_key 예: "normalized/trips", "timetable_rows" 등
                sheet_base = sheet_key.replace("normalized/", "").replace(".csv", "")
                if source_base in sheet_base or sheet_base in source_base:
                    matched_sheet = sheet_key
                    break

            if not matched_sheet:
                continue
            if (matched_sheet, column) in checked_columns:
                continue  # Layer 1에서 이미 검사됨

            col_info = files[matched_sheet].get("columns", {}).get(column)
            if not col_info:
                continue

            total = col_info.get("total_count", 0)
            unique = col_info.get("unique_count", 0)
            if total > 0 and unique < total:
                dup_count = total - unique
                issues.append({
                    "layer": "constraint",
                    "sheet": matched_sheet,
                    "column": column,
                    "set": set_name,
                    "type": "duplicate_set_element",
                    "count": dup_count,
                    "message": (
                        f"Set {set_name}의 원소 {matched_sheet}:{column} — "
                        f"{dup_count}건 중복 (솔버 결과에 영향)"
                    ),
                })

    return issues


def _build_warnings(summary: Dict) -> List[str]:
    """경고 메시지 생성"""
    warnings = []

    if summary["time_columns"]:
        warnings.append(
            f"시간형 컬럼 {len(summary['time_columns'])}개 감지 — "
            f"솔버 사용 시 숫자(분) 변환 필요: {', '.join(summary['time_columns'][:5])}"
        )

    if summary["high_null_columns"]:
        warnings.append(
            f"결측률 50% 이상 컬럼 {len(summary['high_null_columns'])}개: "
            f"{', '.join(summary['high_null_columns'][:5])}"
        )

    if summary["non_tabular_sheets"]:
        warnings.append(
            f"비정형 블록 구조 시트 {len(summary['non_tabular_sheets'])}개 감지: "
            f"{', '.join(summary['non_tabular_sheets'])} — "
            f"표준 source_column 매핑이 불가능할 수 있음"
        )

    return warnings
