# engine/compiler/base.py
# ============================================================
# Model Compiler Base: IR JSON -> Solver-specific model
# ============================================================

from __future__ import annotations

import os
import logging
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

        self._loaded = True
        logger.info(f"DataBinder loaded {len(self._dataframes)} dataframes")

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
        """IR의 set 정의에서 실제 고유값 목록을 추출"""
        source_file = set_def.get("source_file", "")
        source_col = set_def.get("source_column", "")

        if not source_file or not source_col:
            logger.warning(f"Set {set_def.get('id')}: no source_file or source_column")
            return []

        df = self.get_dataframe(source_file)
        if df is None:
            logger.warning(f"Set {set_def.get('id')}: file not found: {source_file}")
            return []

        if source_col not in df.columns:
            # 유사 컬럼명 매칭 시도
            for col in df.columns:
                if col.strip().lower() == source_col.strip().lower():
                    source_col = col
                    break
            else:
                logger.warning(f"Set {set_def.get('id')}: column not found: {source_col} in {source_file}")
                return []

        values = df[source_col].dropna().unique().tolist()
        logger.info(f"Set {set_def.get('id')}: {len(values)} unique values from {source_file}::{source_col}")
        return values

    def get_parameter_values(self, param_def: Dict) -> Any:
        """IR의 parameter 정의에서 실제 값을 추출"""
        # 고정 상수인 경우
        if param_def.get("value") is not None:
            return param_def["value"]

        source_file = param_def.get("source_file", "")
        source_col = param_def.get("source_column", "")

        if not source_file or not source_col:
            return None

        df = self.get_dataframe(source_file)
        if df is None:
            return None

        if source_col not in df.columns:
            for col in df.columns:
                if col.strip().lower() == source_col.strip().lower():
                    source_col = col
                    break
            else:
                return None

        return df[source_col].tolist()

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

        # Sets 바인딩
        for s in math_model.get("sets", []):
            sid = s.get("id", "")
            values = self.get_set_values(s)
            bound["sets"][sid] = values
            bound["set_sizes"][sid] = len(values)

        # Parameters 바인딩
        for p in math_model.get("parameters", []):
            pid = p.get("id", "")
            values = self.get_parameter_values(p)
            bound["parameters"][pid] = values

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
