"""
engine/compiler/data_binder.py
──────────────────────────────
IR(수학 모델)의 sets/parameters에 정의된 데이터 소스를
실제 업로드 파일에서 읽어 바인딩하는 모듈.

원래 engine/compiler/base.py에서 분리됨.
base.py에서 re-export하여 기존 import 경로 호환.
"""

from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

import pandas as pd

from engine.gates.block_parser import parse_blocks, integrate_with_databinder

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("uploads").resolve()


class DataBinder:
    """
    수학 모델 IR의 sets/parameters에 정의된 source_file, source_column을
    실제 업로드된 파일에서 읽어서 바인딩한다.
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.upload_dir = self._get_upload_dir(project_id)
        self._dataframes: Dict[str, pd.DataFrame] = {}
        self._loaded = False

    def _get_upload_dir(self, project_id: str) -> Path:
        import re
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(project_id))
        return (BASE_UPLOAD_DIR / safe_id).resolve()

    def load_files(self) -> None:
        """업로드 디렉토리의 모든 CSV/Excel 파일을 로드"""
        if self._loaded:
            return

        if not self.upload_dir.exists():
            logger.warning(f"Upload dir not found: {self.upload_dir}")
            return

        # ★ 정규화 완료 시 normalized/ 만 로드 (원본 스킵)
        norm_dir = self.upload_dir / "normalized"
        if norm_dir.exists() and norm_dir.is_dir() and any(norm_dir.iterdir()):
            logger.info(f"Normalized data found — loading ONLY normalized/ files")
            for nf in norm_dir.iterdir():
                nfname = nf.name.lower()
                if nfname.endswith(".csv"):
                    try:
                        ndf = pd.read_csv(nf, encoding="utf-8")
                        self._dataframes[f"normalized/{nf.name}"] = ndf
                        logger.info(f"Loaded normalized: {nf.name} ({len(ndf)} rows, {len(ndf.columns)} cols)")
                    except Exception as ne:
                        logger.warning(f"Failed to load normalized {nf.name}: {ne}")
            self._loaded = True
            logger.info(f"DataBinder loaded {len(self._dataframes)} dataframes (normalized only)")
            return



        for fpath in self.upload_dir.iterdir():
            fname = fpath.name.lower()
            try:
                if fname.endswith(".csv"):
                    try:
                        df = pd.read_csv(fpath, encoding="utf-8")
                    except UnicodeDecodeError:
                        df = pd.read_csv(fpath, encoding="cp949")
                    self._dataframes[fpath.name] = df
                    logger.info(f"Loaded CSV: {fpath.name} ({len(df)} rows)")

                elif fname.endswith((".xlsx", ".xls")):
                    engine = "openpyxl" if fname.endswith(".xlsx") else "xlrd"
                    xls = pd.ExcelFile(fpath, engine=engine)
                    for sheet in xls.sheet_names:
                        df = pd.read_excel(xls, sheet_name=sheet)
                        key = f"{fpath.name}::{sheet}"
                        self._dataframes[key] = df
                        # 파일명만으로도 접근 가능하게 (첫 시트)
                        if fpath.name not in self._dataframes:
                            self._dataframes[fpath.name] = df
                    logger.info(f"Loaded Excel: {fpath.name} ({len(xls.sheet_names)} sheets)")

            except Exception as e:
                logger.error(f"Failed to load {fpath.name}: {e}")

        # ★ 비정형 블록 구조 자동 파싱
        self._parse_non_tabular_sheets()


        # ★ 정규화 데이터 우선 로드
        norm_dir = self.upload_dir / "normalized"
        if norm_dir.exists() and norm_dir.is_dir():
            _norm_count = 0
            for nf in norm_dir.iterdir():
                nfname = nf.name.lower()
                if nfname.endswith(".csv"):
                    try:
                        ndf = pd.read_csv(nf, encoding="utf-8")
                        norm_key = f"normalized/{nf.name}"
                        self._dataframes[norm_key] = ndf
                        _norm_count += 1
                        logger.info(f"Loaded normalized: {nf.name} ({len(ndf)} rows, {len(ndf.columns)} cols)")
                    except Exception as ne:
                        logger.warning(f"Failed to load normalized {nf.name}: {ne}")
            if _norm_count > 0:
                logger.info(f"Loaded {_norm_count} normalized files (these take priority in data_guide)")

        self._loaded = True
        logger.info(f"DataBinder loaded {len(self._dataframes)} dataframes")


    def _parse_non_tabular_sheets(self) -> None:
        """비정형 블록 구조 시트를 감지하고 파싱하여 요약 테이블 추가"""
        keys_to_parse = []
        parsed_files = set()
        for key, df in list(self._dataframes.items()):
            # 이미 파싱된 요약/블록 테이블은 스킵
            if "__summary" in key or "__DIA" in key or "__Block" in key:
                continue
            # :: 없는 키(파일명 전용 단축 키)는 중복이므로 스킵
            if "::" not in key:
                continue
            if self._is_non_tabular(df):
                keys_to_parse.append(key)

        for key in keys_to_parse:
            try:
                df = self._dataframes[key]
                # header=None으로 다시 읽어야 하므로 원본 파일에서 재로드
                raw_df = self._reload_without_header(key)
                if raw_df is None:
                    continue

                result = parse_blocks(raw_df)
                if result["block_count"] > 0:
                    integrate_with_databinder(result, self._dataframes, key)
                    # 파싱 완료된 키 기록 (중복 방지)
                    parsed_files.add(key)
                    logger.info(
                        f"BlockParser: '{key}' -> {result['block_count']} blocks, "
                        f"summary added as '{key}__summary'"
                    )
            except Exception as e:
                logger.warning(f"BlockParser failed for '{key}': {e}")

    def _is_non_tabular(self, df: pd.DataFrame) -> bool:
        """비정형 블록 구조인지 간단히 판단"""
        if len(df) < 10 or len(df.columns) < 3:
            return False

        # 블록 파서가 생성한 테이블은 정형
        if hasattr(df, 'attrs') and df.attrs.get('_parsed_by_block_parser'):
            return False

        # 첫 번째 컬럼에 헤더 값이 반복되는지 체크
        first_col = df.iloc[:, 0].astype(str)
        if len(first_col) == 0:
            return False

        # NaN 비율이 높으면 비정형 가능성
        null_ratio = df.isna().sum().sum() / (len(df) * len(df.columns))
        if null_ratio > 0.3:
            return True

        # 첫 번째 컬럼에서 동일 값이 3회 이상 반복
        value_counts = first_col.value_counts()
        if len(value_counts) > 0 and value_counts.iloc[0] >= 3:
            top_val = value_counts.index[0]
            if top_val != "nan":
                return True

        return False

    def _reload_without_header(self, key: str) -> pd.DataFrame:
        """시트를 header=None으로 다시 로드"""
        try:
            if "::" in key:
                fname, sheet = key.split("::", 1)
            else:
                fname = key
                sheet = 0

            fpath = self.upload_dir / fname
            if not fpath.exists():
                # 파일명 부분 매칭
                for p in self.upload_dir.iterdir():
                    if fname in p.name:
                        fpath = p
                        break

            if not fpath.exists():
                logger.warning(f"Cannot reload '{key}': file not found")
                return None

            engine = "openpyxl" if str(fpath).endswith(".xlsx") else "xlrd"
            return pd.read_excel(fpath, sheet_name=sheet, header=None, engine=engine)
        except Exception as e:
            logger.warning(f"Failed to reload '{key}' without header: {e}")
            return None

    def get_dataframe(self, source_file: str) -> Optional[pd.DataFrame]:
        """파일명으로 DataFrame 반환"""
        self.load_files()
        # 정확한 이름 매칭
        if source_file in self._dataframes:
            return self._dataframes[source_file]
        # 부분 매칭 (파일명에 확장자 없이 올 수도 있음)
        for key, df in self._dataframes.items():
            if source_file in key or key.startswith(source_file):
                return df
        return None

    def get_set_values(self, set_def: Dict) -> List[Any]:
        """
        세트 정의에서 값 목록 추출.
        지원 방식:
        1. source_type: "range" + size/range_param -> [1, 2, ..., N]
        2. source_type: "explicit" + values -> 직접 지정된 값
        3. source_file + source_column -> CSV/Excel 열에서 고유값
        4. values 필드 -> 직접 지정된 값 (fallback)
        """
        set_id = set_def.get("id", "?")

        # 1. range 타입: 가상 인덱스 생성
        source_type = set_def.get("source_type", "")
        if source_type == "range":
            size = set_def.get("size", 0)
            if not size:
                range_param = set_def.get("range_param", "")
                if range_param and hasattr(self, '_bound_params'):
                    size = self._bound_params.get(range_param, 0)
            if isinstance(size, str):
                try:
                    size = int(size)
                except ValueError:
                    size = 0
            if size > 0:
                values = list(range(1, size + 1))
                logger.info(f"Set {set_id}: {len(values)} values from range(1..{size})")
                return values
            else:
                logger.warning(f"Set {set_id}: range type but size=0")
                return []

        # 2. explicit 타입 또는 values 필드
        if source_type == "explicit" or (not set_def.get("source_file") and set_def.get("values")):
            values = set_def.get("values", [])
            logger.info(f"Set {set_id}: {len(values)} explicit values")
            return values


        # 2.5. file 타입: JSON 파일에서 직접 로드
        if source_type == "file":
            source_file = set_def.get("source_file", "")
            if source_file:
                import json as _json
                # upload_dir 기준으로 파일 탐색
                _candidates = [
                    self.upload_dir / source_file,
                    self.upload_dir / "normalized" / Path(source_file).name,
                ]
                for _cand in _candidates:
                    if _cand.exists():
                        try:
                            with open(str(_cand), encoding="utf-8") as _sf:
                                file_values = _json.load(_sf)
                            if isinstance(file_values, list):
                                # tuple로 변환 (pair 데이터)
                                file_values = [tuple(v) if isinstance(v, list) else v for v in file_values]
                                logger.info(f"Set {set_id}: {len(file_values)} values from file {_cand.name}")
                                return file_values
                        except Exception as _fe:
                            logger.warning(f"Set {set_id}: file load error {_cand}: {_fe}")
            logger.warning(f"Set {set_id}: source_type=file but file not found")
            return []

        # 3. source_file + source_column
        source_file = set_def.get("source_file", "")
        source_column = set_def.get("source_column", "")

        if not source_file or not source_column:
            values = set_def.get("values", [])
            if values:
                logger.info(f"Set {set_id}: {len(values)} values from 'values' field")
                return values
            logger.warning(f"Set {set_id}: no source_file/source_column and no values")
            return []

        df = self.get_dataframe(source_file)
        if df is None:
            logger.warning(f"Set {set_id}: file '{source_file}' not found")
            return []

        if source_column not in df.columns:
            logger.warning(f"Set {set_id}: column '{source_column}' not in {source_file}")
            return []

        values = df[source_column].dropna().unique().tolist()
        logger.info(f"Set {set_id}: {len(values)} unique values from {source_file}::{source_column}")
        return values

    def get_parameter_values(self, param_def: Dict) -> Any:
        """IR의 parameter 정의에서 실제 값을 추출"""
        import logging
        _logger = logging.getLogger(__name__)

        # 1) default_value가 있으면 최우선 사용 (사용자가 문제정의에서 확정한 값)
        default_val = param_def.get("default_value")
        if default_val is not None:
            _logger.info(f"Parameter {param_def.get('id')!r}: {default_val} [source: confirmed_problem]")
            return default_val

        # 1.5) value 필드가 있으면 사용 (수학모델 IR에서 직접 지정된 값)
        direct_val = param_def.get("value")
        if direct_val is not None:
            _logger.info(f"Parameter {param_def.get('id')!r}: {direct_val} [source: math_model_ir]")
            return direct_val

        # 2) 데이터 소스에서 가져오기 시도
        source_file = param_def.get("source_file", "")
        source_col = param_def.get("source_column", "")

        # parameters.csv의 value 컬럼은 전체 배열이므로 스칼라 파라미터에 부적합
        # source_column이 "value"이고 source_file이 parameters.csv이면 스킵
        if source_file and "parameters.csv" in source_file and source_col == "value":
            _logger.warning(f"Parameter {param_def.get('id')!r}: skipping parameters.csv bulk read (no default_value)")
            return None

        if source_file and source_col:
            df = self.get_dataframe(source_file)
            if df is not None:
                if source_col not in df.columns:
                    for col in df.columns:
                        if col.strip().lower() == source_col.strip().lower():
                            source_col = col
                            break
                    else:
                        _logger.warning(f"Parameter '{param_def.get('id')}': column '{source_col}' not found in {source_file} [source: csv_lookup_failed]")
                        # 소스 실패 시 default_value 또는 value 사용
                        if default_val is not None:
                            return default_val
                        if param_def.get("value") is not None:
                            return param_def["value"]
                        return None
                # key_column이 있으면 {key: value} dict로 반환 (indexed 파라미터)
                key_col = param_def.get("key_column", "")
                if key_col and key_col in df.columns:
                    pid = param_def.get("id", "")
                    result_dict = {}
                    for _, row in df.iterrows():
                        k = row[key_col]
                        v = row[source_col]
                        import math as _math
                        if v is None or (isinstance(v, float) and _math.isnan(v)):
                            continue
                        if isinstance(v, float) and v == int(v):
                            v = int(v)
                        result_dict[k] = v
                        result_dict[str(k)] = v  # 문자열 키도 동시 등록
                    _logger.info(f"Parameter {pid!r}: {len(result_dict)//2} indexed values [source: csv:{source_file}::{source_col}, key={key_col}]")
                    return result_dict

                # param_name 컬럼이 있으면 파라미터 id로 필터링하여 스칼라 반환
                if "param_name" in df.columns and source_col == "value":
                    pid = param_def.get("id", "")
                    matched = df[df["param_name"] == pid]
                    if len(matched) == 1:
                        val = matched.iloc[0][source_col]
                        # 빈 값/NaN이면 default_value로 fallback
                        import math
                        if val is None or (isinstance(val, str) and val.strip() == '') or (isinstance(val, float) and math.isnan(val)):
                            if default_val is not None:
                                _logger.info(f"Parameter {pid!r}: {default_val} [source: confirmed_problem, csv_value_empty]")
                                return default_val
                        _logger.info(f"Parameter {pid!r}: {val} [source: csv:{source_file}, param_name_lookup]")
                        return val
                    elif len(matched) > 1:
                        val = matched.iloc[0][source_col]
                        _logger.warning(f"Parameter {pid!r}: multiple matches ({len(matched)}), using first: {val}")
                        return val
                    # param_name에 없으면 전체 컬럼 반환 (기존 동작)
                return df[source_col].tolist()

        # 3) 소스가 없을 때: default_value > value 순서로 반환
        if default_val is not None:
            _logger.info(f"Parameter {param_def.get('id')!r}: {default_val} [source: fallback_default, no_csv_source]")
            return default_val
        if param_def.get("value") is not None:
            _logger.info(f"Parameter {param_def.get('id')!r}: {param_def['value']} [source: fallback_ir_value, no_csv_source]")
            return param_def["value"]
        _logger.warning(f"Parameter {param_def.get('id')!r}: None [source: not_found, no value available]")
        return None

    @staticmethod
    def _determine_source(param_def: dict, resolved_value) -> str:
        """파라미터 값이 어디서 왔는지 결정 (F5 source tracking)."""
        if resolved_value is None:
            return "not_found"
        if param_def.get("default_value") is not None:
            return "confirmed_problem"
        if param_def.get("value") is not None:
            return "math_model_ir"
        sf = param_def.get("source_file", "")
        sc = param_def.get("source_column", "")
        if sf and sc:
            return f"csv:{sf}::{sc}"
        if param_def.get("auto_injected"):
            return "auto_inject"
        return "fallback"

    @staticmethod
    def _convert_time_values(values):
        """datetime.time 값을 분(minutes) 정수로 변환"""
        import datetime
        if values is None:
            return None
        if isinstance(values, datetime.time):
            return values.hour * 60 + values.minute + round(values.second / 60)
        if isinstance(values, datetime.timedelta):
            return int(values.total_seconds() / 60)
        if isinstance(values, list):
            result = []
            for v in values:
                if isinstance(v, datetime.time):
                    result.append(v.hour * 60 + v.minute + round(v.second / 60))
                elif isinstance(v, datetime.timedelta):
                    result.append(int(v.total_seconds() / 60))
                elif v is None:
                    result.append(0)
                else:
                    result.append(v)
            return result
        if isinstance(values, dict):
            result = {}
            for k, v in values.items():
                if isinstance(v, datetime.time):
                    result[k] = v.hour * 60 + v.minute + round(v.second / 60)
                elif isinstance(v, datetime.timedelta):
                    result[k] = int(v.total_seconds() / 60)
                elif v is None:
                    result[k] = 0
                else:
                    result[k] = v
            return result
        return values

    def bind_all(self, math_model: Dict) -> Dict[str, Any]:
        """
        IR 전체를 바인딩하여 실제 데이터가 채워진 딕셔너리를 반환.

        Returns:
            {
                "sets": { "S": [값1, 값2, ...], "C": [...], ... },
                "parameters": { "param_id": [값] 또는 스칼라, ... },
                "set_sizes": { "S": 96, "C": 25, ... },
            }
        """
        self.load_files()

        bound = {
            "sets": {},
            "parameters": {},
            "set_sizes": {},
            "parameter_sources": {},  # F5: source tracking
            "parameter_warnings": [],  # F11: validation warnings
        }

        # Parameters 먼저 바인딩 (set 보정에 필요)
        for p in math_model.get("parameters", []):
            pid = p.get("id", "")
            values = self.get_parameter_values(p)
            # datetime.time -> 분(minutes) 정수 변환
            values = self._convert_time_values(values)
            bound["parameters"][pid] = values
            # source tracking
            source = self._determine_source(p, values)
            bound["parameter_sources"][pid] = source

        # ── Parameter alias: expression에서 사용하는 일반 이름에 대한 fallback 매핑 ──
        import re as _re
        # 공통 예약어/변수명 (도메인 무관)
        _known_non_params = {"x", "y", "i", "j", "i1", "i2",
                             "for", "in", "sum", "if", "else",
                             "and", "or", "not"}
        # 도메인별 변수명은 math_model의 variables/sets에서 동적 수집
        for _v in math_model.get("variables", math_model.get("decision_variables", [])):
            _vid = _v.get("id", "")
            if _vid:
                _known_non_params.add(_vid)
        for _s in math_model.get("sets", []):
            _sid = _s.get("id", "")
            if _sid:
                _known_non_params.add(_sid)
        def _collect_param_names_from_node(node):
            """구조화된 lhs/rhs JSON 노드에서 param 이름을 재귀 수집"""
            if not isinstance(node, dict):
                return set()
            names = set()
            if 'param' in node:
                pi = node['param']
                if isinstance(pi, dict):
                    n = pi.get('name', '')
                elif isinstance(pi, str):
                    n = pi
                else:
                    n = ''
                if n and n not in _known_non_params:
                    names.add(n)
            # 재귀: sum, add, subtract, multiply, negate, lhs, rhs 등 하위 노드
            for key in ('lhs', 'rhs', 'negate'):
                if key in node and isinstance(node[key], dict):
                    names.update(_collect_param_names_from_node(node[key]))
            for key in ('sum', 'add', 'subtract', 'multiply'):
                child = node.get(key)
                if isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            names.update(_collect_param_names_from_node(item))
                elif isinstance(child, dict):
                    names.update(_collect_param_names_from_node(child))
            return names

        _expr_params = set()
        for _c in math_model.get("constraints", []):
            # expression/expression_template 텍스트에서 추출
            _expr = _c.get("expression", "") or _c.get("expression_template", "") or ""
            _words = set(_re.findall(r'[a-z][a-z_]+[a-z]', _expr))
            _expr_params.update(_words - _known_non_params)
            # 구조화된 lhs/rhs JSON 노드에서도 추출
            for _side in ('lhs', 'rhs'):
                _node = _c.get(_side)
                if isinstance(_node, dict):
                    _expr_params.update(_collect_param_names_from_node(_node))

        for _name in _expr_params:
            if _name in bound["parameters"] and bound["parameters"][_name] is not None:
                continue
            # prefix match: cleanup_minutes -> cleanup_minutes_arrival (shortest suffix = most specific base)
            _candidates = {k: v for k, v in bound["parameters"].items()
                           if k.startswith(_name + "_") and v is not None
                           and not isinstance(v, (dict, list, tuple))}
            if _candidates:
                # 이름이 가장 짧은 candidate 선택 (base form에 가장 가까움)
                # night/relay 등 context-specific 변형보다 arrival/departure 같은 기본형 우선
                _best = min(_candidates, key=lambda k: len(k))
                bound["parameters"][_name] = _candidates[_best]
                logger.info(f"Param alias (prefix): '{_name}' -> '{_best}' = {_candidates[_best]}")
                continue
            # token overlap match: min_night_rest_minutes -> min_night_rest_total_minutes
            _name_tokens = set(_name.split("_"))
            _best_key, _best_score, _best_val = None, 0, None
            for _k, _v in bound["parameters"].items():
                if _v is None or isinstance(_v, (dict, list, tuple)):
                    continue
                _k_tokens = set(_k.split("_"))
                _overlap = len(_name_tokens & _k_tokens)
                if _overlap > _best_score and _overlap >= min(3, len(_name_tokens)):
                    _best_score, _best_key, _best_val = _overlap, _k, _v
            if _best_key:
                bound["parameters"][_name] = _best_val
                logger.info(f"Param alias (token): '{_name}' -> '{_best_key}' = {_best_val}")

        # Sets 바인딩 (자동 range 보정 포함)
        for s in math_model.get("sets", []):
            sid = s.get("id", "")
            values = self.get_set_values(s)

            # 자동 보정: set이 변수의 인덱스로 사용되는데 크기가 비정상적으로 작은 경우
            # 파라미터에서 실제 크기를 찾아 range로 대체
            if len(values) < 20:
                # 변수 정의에서 이 set을 인덱스로 사용하는지 확인
                is_var_index = False
                for var_def in math_model.get("variables", []):
                    if sid in var_def.get("indices", []):
                        is_var_index = True
                        break

                if is_var_index:
                    # 파라미터에서 이 set의 크기 정보 찾기
                    size_param_names = [
                        f"total_{s.get('name', '').lower()}",
                        f"total_{sid.lower()}",
                        f"total_{s.get('name', '').lower()}_count",
                        f"{s.get('name', '').lower()}_count",
                        "total_crew", "total_crew_count",
                        "num_crews", "num_workers", "num_employees",
                    ]
                    for pname in size_param_names:
                        pval = bound["parameters"].get(pname)
                        if pval is not None:
                            try:
                                size = int(float(str(pval)))
                            except (ValueError, TypeError):
                                continue
                            if size > len(values):
                                logger.info(f"Set {sid}: auto-corrected from {len(values)} items to range(1..{size}) using param '{pname}'")
                                values = list(range(1, size + 1))
                                break

            # 자동 보정: set이 crew/duty 인덱스인데 크기가 total_duties보다 과대한 경우
            # (LLM이 데이터의 다른 숫자를 잘못 사용했을 때)
            if len(values) > 50 and sid in ("J", "K", "crews", "duties"):
                duty_params = ["total_duties", "total_crew_count", "total_crew"]
                for pname in duty_params:
                    pval = bound["parameters"].get(pname)
                    if pval is not None:
                        try:
                            expected = int(float(str(pval)))
                        except (ValueError, TypeError):
                            continue
                        if 0 < expected < len(values):
                            logger.warning(
                                f"Set {sid}: size {len(values)} exceeds param '{pname}'={expected}, "
                                f"auto-correcting to range(1..{expected})"
                            )
                            values = list(range(1, expected + 1))
                            break

            bound["sets"][sid] = values
            bound["set_sizes"][sid] = len(values)


        # ── Auto-bind: 제약조건에서 source_file/source_column이 있는 param 참조를 자동 바인딩 ──
        def _scan_params(node, found):
            """제약조건 트리를 재귀 스캔하여 source_file이 있는 param 노드 수집"""
            if isinstance(node, dict):
                if "param" in node and isinstance(node["param"], dict):
                    p = node["param"]
                    sf = p.get("source_file", "")
                    sc = p.get("source_column", "")
                    pname = p.get("name", "")
                    if sf and sc and pname and pname not in found:
                        found[pname] = {"source_file": sf, "source_column": sc}
                for v in node.values():
                    _scan_params(v, found)
            elif isinstance(node, list):
                for item in node:
                    _scan_params(item, found)

        ref_params = {}
        for con in math_model.get("constraints", []):
            _scan_params(con, ref_params)
        _scan_params(math_model.get("objective", {}), ref_params)

        for pname, pinfo in ref_params.items():
            if pname in bound["parameters"]:
                continue
            sf = pinfo["source_file"]
            sc = pinfo["source_column"]
            df = self.get_dataframe(sf)
            if df is None:
                logger.warning(f"Auto-bind: file {sf!r} not found for param {pname!r}")
                continue
            if sc not in df.columns:
                logger.warning(f"Auto-bind: column {sc!r} not in {sf!r} for param {pname!r}")
                continue
            values = df[sc].tolist()
            values = self._convert_time_values(values)
            bound["parameters"][pname] = values
            logger.info(f"Auto-bind: {pname!r} <- {sf}::{sc} ({len(values)} values)")

        # ── Trip 인덱스 데이터 자동 주입 ──
        # preparation_time, cleanup_time, max_driving_time 등의 표현식에서
        # trip_dep_time[i], trip_arr_time[i], trip_duration[i]를 계수로 사용.
        # model.json의 parameters에 없으면 expression_parser가 0을 반환하여
        # duty_start[j] <= -prep_minutes → INFEASIBLE 발생.
        # 구모델 호환을 위해 항상 trips.csv에서 자동 로드(없는 경우에만).
        _TRIP_INDEX_PARAMS = {
            "trip_dep_time": "trip_dep_time",
            "trip_arr_time": "trip_arr_time",
            "trip_duration": "trip_duration",
        }
        _trips_df = None
        for _tp_id, _tp_col in _TRIP_INDEX_PARAMS.items():
            if bound["parameters"].get(_tp_id) is not None:
                continue  # 이미 바인딩됨
            # trips.csv 로드 (한 번만)
            if _trips_df is None:
                _trips_df = self.get_dataframe("normalized/trips.csv")
            if _trips_df is None or "trip_id" not in _trips_df.columns or _tp_col not in _trips_df.columns:
                logger.warning(f"Trip auto-inject: cannot load '{_tp_id}' (trips.csv missing or no trip_id column)")
                continue
            import math as _math
            _result = {}
            for _, _row in _trips_df.iterrows():
                _k = _row["trip_id"]
                _v = _row[_tp_col]
                if _v is None or (isinstance(_v, float) and _math.isnan(_v)):
                    continue
                if isinstance(_v, float) and _v == int(_v):
                    _v = int(_v)
                _result[_k] = _v
                _result[str(_k)] = _v
            bound["parameters"][_tp_id] = _result
            bound["parameter_sources"][_tp_id] = "auto_inject:trips.csv"
            logger.info(f"Trip auto-inject: '{_tp_id}' loaded {len(_result)//2} values from trips.csv")

        # ── Policy-Driven Canonical Field Generation ──
        _domain = math_model.get("domain", "") or math_model.get("metadata", {}).get("domain", "")
        if _domain:
            try:
                from engine.policy import PolicyEngine, PolicyResolutionContext
                _policy_engine = PolicyEngine(_domain)
                if _policy_engine.has_policies():
                    _ctx = PolicyResolutionContext(
                        domain=_domain,
                        clarification_params=bound["parameters"],
                    )
                    _resolved = _policy_engine.resolve(_ctx)
                    _canonical = _policy_engine.generate_canonical_fields(bound, _resolved)

                    bound["_policy_result"] = {
                        "resolved": _resolved.to_dict(),
                        "canonical": _canonical.to_dict(),
                        "provenance": _canonical.provenance,
                    }
                    bound["_policy_adjustments"] = {
                        "variable_bounds": _policy_engine.get_variable_bound_adjustments(_resolved),
                        "big_m": _canonical.param_adjustments.get("big_m"),
                    }
                    logger.info(
                        f"PolicyEngine: {len(_canonical.fields_created)} canonical fields, "
                        f"hash={_resolved.resolved_hash}"
                    )
            except Exception as _pe_err:
                # fail-closed if model references canonical fields
                _canonical_refs = any(
                    "abs_minute" in str(c.get("expression_template", ""))
                    for c in math_model.get("constraints", [])
                    if isinstance(c, dict)
                )
                if _canonical_refs:
                    raise RuntimeError(
                        f"PolicyEngine failed but model references canonical fields: {_pe_err}"
                    ) from _pe_err
                logger.warning(f"PolicyEngine failed (non-blocking): {_pe_err}")

        # ── F8: Binding summary log ──
        total_params = len(bound["parameters"])
        bound_params = sum(1 for v in bound["parameters"].values() if v is not None)
        unbound = [k for k, v in bound["parameters"].items() if v is None]
        logger.info(
            f"DataBinder summary: {bound_params}/{total_params} params bound, "
            f"{len(bound['sets'])} sets loaded, "
            f"unbound: {unbound if unbound else 'none'}"
        )

        # ── F11: Parameter validation (range check against math_model definitions) ──
        for p in math_model.get("parameters", []):
            pid = p.get("id", "")
            val = bound["parameters"].get(pid)
            if val is None or isinstance(val, (dict, list)):
                continue
            try:
                fval = float(val)
            except (ValueError, TypeError):
                continue
            # 음수 시간 파라미터 체크
            if pid.endswith("_minutes") or pid.endswith("_time") or pid.endswith("_min"):
                if fval < 0:
                    msg = f"Parameter '{pid}' = {fval}: 음수 시간 값 (바인딩 오류 가능)"
                    bound["parameter_warnings"].append(msg)
                    logger.warning(msg)
            # 비현실적으로 큰 값 체크
            if pid.endswith("_minutes") and fval > 2880:  # 48시간 초과
                msg = f"Parameter '{pid}' = {fval}: 48시간 초과 (비현실적 값)"
                bound["parameter_warnings"].append(msg)
                logger.warning(msg)

        return bound
