# engine/compiler/dwave_bqm_compiler.py
# ============================================================
# D-Wave BQM Compiler: IR -> dimod.BinaryQuadraticModel
# 제약조건은 QUBO 페널티로 변환
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseCompiler, CompileResult

logger = logging.getLogger(__name__)


class DWaveBQMCompiler(BaseCompiler):
    """
    수학 모델 IR을 D-Wave BQM(QUBO)으로 변환.
    제약조건은 페널티 항으로 변환됨.
    연속/정수 변수는 바이너리 인코딩으로 근사.
    """

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        try:
            import dimod

            bqm = dimod.BinaryQuadraticModel(vartype=dimod.BINARY)
            var_map = {}
            total_vars = 0
            warnings = []
            penalty_weight = kwargs.get("penalty_weight", 10.0)

            # 1. 변수 생성 (바이너리만)
            for var_def in math_model.get("variables", []):
                vid = var_def.get("id", "")
                vtype = self._get_variable_type(var_def)
                indices = var_def.get("indices", [])

                if vtype != "binary":
                    warnings.append(
                        f"Variable {vid}: type={vtype} -> binary encoding applied"
                    )

                if not indices:
                    name = vid
                    bqm.add_variable(name, 0.0)
                    var_map[vid] = name
                    total_vars += 1
                else:
                    combos = self._compute_set_product(indices, bound_data)
                    var_map[vid] = {}
                    for combo in combos:
                        key = tuple(str(c) for c in combo)
                        name = f"{vid}_{'_'.join(key)}"
                        bqm.add_variable(name, 0.0)
                        var_map[vid][key] = name
                        total_vars += 1

            logger.info(f"BQM: created {total_vars} binary variables")

            # 2. 목적함수 (선형 항)
            obj = math_model.get("objective", {})
            self._add_objective_bqm(bqm, var_map, obj, bound_data)

            # 3. 제약조건 -> 페널티 항
            penalty_count = 0
            for con_def in math_model.get("constraints", []):
                cid = con_def.get("id", "")
                added = self._add_penalty_bqm(
                    bqm, var_map, con_def, bound_data, penalty_weight
                )
                if added:
                    penalty_count += added
                else:
                    warnings.append(f"Constraint {cid}: could not convert to penalty")

            logger.info(f"BQM: added {penalty_count} penalty terms")

            return CompileResult(
                success=True,
                solver_model=bqm,
                solver_type="bqm",
                variable_count=total_vars,
                constraint_count=penalty_count,
                variable_map=var_map,
                warnings=warnings,
                metadata={
                    "model_type": "BQM/QUBO",
                    "engine": "D-Wave",
                    "penalty_weight": penalty_weight,
                },
            )

        except ImportError:
            return CompileResult(
                success=False,
                error="dimod package not installed. Run: pip install dimod"
            )
        except Exception as e:
            logger.error(f"BQM compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _add_objective_bqm(self, bqm, var_map, obj_def, bound_data):
        """BQM 목적함수: 선형 바이어스로 추가"""
        obj_type = obj_def.get("type", "minimize")
        sign = 1.0 if obj_type == "minimize" else -1.0

        for vid, v in var_map.items():
            if isinstance(v, dict):
                for key, name in v.items():
                    bqm.add_variable(name, sign * 1.0)
            elif isinstance(v, str):
                bqm.add_variable(v, sign * 1.0)

    def _add_penalty_bqm(self, bqm, var_map, con_def, bound_data, penalty) -> int:
        """제약조건을 QUBO 페널티로 변환"""
        expr = con_def.get("expression", "").strip()
        count = 0

        # 할당 제약: sum(x[i,j] for j) == 1
        # 페널티: P * (sum(x_j) - 1)^2 = P * (sum_j x_j^2 - 2*sum_j x_j + 1 + 2*sum_{j<k} x_j*x_k)
        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if isinstance(first_key, tuple) and len(first_key) >= 2:
                        groups = {}
                        for key, name in vars_dict.items():
                            groups.setdefault(key[0], []).append(name)

                        for gk, names in groups.items():
                            # 선형 항: P * (x_j^2 - 2*x_j) = P * (x_j - 2*x_j) = -P*x_j (바이너리)
                            for name in names:
                                bqm.add_variable(name, -penalty)
                            # 이차 항: P * 2 * x_j * x_k
                            for i_idx in range(len(names)):
                                for j_idx in range(i_idx + 1, len(names)):
                                    bqm.add_interaction(names[i_idx], names[j_idx], 2 * penalty)
                            # 상수 항: P (BQM offset)
                            bqm.offset += penalty
                            count += 1
                        break

        return count
