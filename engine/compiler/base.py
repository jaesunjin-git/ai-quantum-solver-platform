# engine/compiler/base.py
# ============================================================
# Model Compiler Base: IR JSON -> Solver-specific model
# ============================================================

from __future__ import annotations

import os
import logging
from engine.gates.block_parser import parse_blocks, integrate_with_databinder
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("uploads").resolve()


# ============================================================
# CompileResult: 컴파일 결과 컨테이너
# ============================================================
@dataclass
class CompileResult:
    """컴파일러 출력"""
    success: bool
    solver_model: Any = None          # 솔버별 모델 객체
    solver_type: str = ""             # "ortools_cp", "ortools_lp", "cqm", "bqm"
    variable_count: int = 0
    constraint_count: int = 0
    variable_map: Dict[str, Any] = field(default_factory=dict)  # IR변수ID -> 솔버변수
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# DataBinder: IR의 source_file/source_column -> 실제 데이터
# ============================================================
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

        # 1) default_value가 있으면 최우선 사용 (사용자 입력값)
        default_val = param_def.get("default_value")

        # 2) 데이터 소스에서 가져오기 시도
        source_file = param_def.get("source_file", "")
        source_col = param_def.get("source_column", "")

        if source_file and source_col:
            df = self.get_dataframe(source_file)
            if df is not None:
                if source_col not in df.columns:
                    for col in df.columns:
                        if col.strip().lower() == source_col.strip().lower():
                            source_col = col
                            break
                    else:
                        _logger.warning(f"Parameter '{param_def.get('id')}': column '{source_col}' not found in {source_file}")
                        # 소스 실패 시 default_value 또는 value 사용
                        if default_val is not None:
                            return default_val
                        if param_def.get("value") is not None:
                            return param_def["value"]
                        return None
                # param_name 컬럼이 있으면 파라미터 id로 필터링하여 스칼라 반환
                if "param_name" in df.columns and source_col == "value":
                    pid = param_def.get("id", "")
                    matched = df[df["param_name"] == pid]
                    if len(matched) == 1:
                        val = matched.iloc[0][source_col]
                        _logger.info(f"Parameter {pid!r}: scalar {val} from param_name lookup")
                        return val
                    elif len(matched) > 1:
                        val = matched.iloc[0][source_col]
                        _logger.warning(f"Parameter {pid!r}: multiple matches ({len(matched)}), using first: {val}")
                        return val
                    # param_name에 없으면 전체 컬럼 반환 (기존 동작)
                return df[source_col].tolist()

        # 3) 소스가 없을 때: default_value > value 순서로 반환
        if default_val is not None:
            return default_val
        if param_def.get("value") is not None:
            return param_def["value"]
        return None

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
        }

        # Parameters 먼저 바인딩 (set 보정에 필요)
        for p in math_model.get("parameters", []):
            pid = p.get("id", "")
            values = self.get_parameter_values(p)
            # datetime.time -> 분(minutes) 정수 변환
            values = self._convert_time_values(values)
            bound["parameters"][pid] = values

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

        return bound


# ============================================================
# BaseCompiler: 추상 컴파일러
# ============================================================
class BaseCompiler(ABC):
    """모든 솔버 컴파일러의 기본 클래스"""

    @abstractmethod
    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """
        수학 모델 IR + 바인딩된 데이터 -> 솔버별 모델 객체

        Args:
            math_model: IR JSON (sets, variables, objective, constraints, ...)
            bound_data: DataBinder.bind_all() 결과
            **kwargs: 솔버별 추가 옵션

        Returns:
            CompileResult
        """
        ...

    def _get_variable_type(self, var_def: Dict) -> str:
        """IR 변수 타입을 정규화"""
        vtype = var_def.get("type", "binary").lower().strip()
        aliases = {
            "numeric": "continuous",
            "float": "continuous",
            "real": "continuous",
            "bool": "binary",
            "boolean": "binary",
            "int": "integer",
        }
        return aliases.get(vtype, vtype)

    def _compute_set_product(self, indices: List[str], bound_data: Dict) -> List[tuple]:
        """변수의 indices에 해당하는 집합들의 데카르트 곱을 계산"""
        from itertools import product

        sets_values = []
        for idx in indices:
            values = bound_data.get("sets", {}).get(idx, [])
            if not values:
                logger.warning(f"Empty set for index: {idx}")
                return []
            sets_values.append(values)

        return list(product(*sets_values))
