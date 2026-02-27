# engine/compiler/dwave_cqm_compiler.py
# ============================================================
# D-Wave CQM Compiler: IR -> dimod.ConstrainedQuadraticModel
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseCompiler, CompileResult

logger = logging.getLogger(__name__)


class DWaveCQMCompiler(BaseCompiler):
    """수학 모델 IR을 D-Wave CQM으로 변환"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        try:
            import dimod

            cqm = dimod.ConstrainedQuadraticModel()
            var_map = {}
            total_vars = 0
            total_constraints = 0
            warnings = []

            # 1. 변수 생성
            for var_def in math_model.get("variables", []):
                vid = var_def.get("id", "")
                vtype = self._get_variable_type(var_def)
                indices = var_def.get("indices", [])

                if not indices:
                    var_map[vid] = self._create_cqm_var(dimod, vid, vtype, var_def)
                    total_vars += 1
                else:
                    combos = self._compute_set_product(indices, bound_data)
                    var_map[vid] = {}
                    for combo in combos:
                        key = tuple(str(c) for c in combo)
                        name = f"{vid}_{'_'.join(key)}"
                        var_map[vid][key] = self._create_cqm_var(dimod, name, vtype, var_def)
                        total_vars += 1

                    if not combos:
                        warnings.append(f"Variable {vid}: no index combinations")

            logger.info(f"CQM: created {total_vars} variables")

            # 2. 제약조건
            for con_def in math_model.get("constraints", []):
                cid = con_def.get("id", "")
                category = con_def.get("category", "hard")
                weight = con_def.get("weight")

                parsed = self._parse_constraint_cqm(
                    cqm, var_map, con_def, bound_data, category, weight
                )
                if parsed:
                    total_constraints += parsed
                else:
                    warnings.append(f"Constraint {cid}: could not parse")

            logger.info(f"CQM: created {total_constraints} constraints")

            # 3. 목적함수
            obj = math_model.get("objective", {})
            obj_parsed = self._parse_objective_cqm(cqm, var_map, obj, bound_data)
            if not obj_parsed:
                warnings.append("Objective: could not parse, using default")

            return CompileResult(
                success=True,
                solver_model=cqm,
                solver_type="cqm",
                variable_count=total_vars,
                constraint_count=total_constraints,
                variable_map=var_map,
                warnings=warnings,
                metadata={"model_type": "CQM", "engine": "D-Wave"},
            )

        except ImportError:
            return CompileResult(
                success=False,
                error="dimod package not installed. Run: pip install dimod"
            )
        except Exception as e:
            logger.error(f"CQM compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _create_cqm_var(self, dimod, name, vtype, var_def):
        """CQM 변수 생성"""
        if vtype == "binary":
            return dimod.Binary(name)
        elif vtype == "integer":
            lb = int(var_def.get("lower_bound") or 0)
            ub = int(var_def.get("upper_bound") or 1000000)
            return dimod.Integer(name, lower_bound=lb, upper_bound=ub)
        else:  # continuous
            lb = float(var_def.get("lower_bound") if var_def.get("lower_bound") is not None else 0)
            ub = float(var_def.get("upper_bound") if var_def.get("upper_bound") is not None else 1e7)
            return dimod.Real(name, lower_bound=lb, upper_bound=ub)

    def _parse_constraint_cqm(self, cqm, var_map, con_def, bound_data, category, weight) -> int:
        """CQM 제약 파싱"""
        expr = con_def.get("expression", "").strip()
        cid = con_def.get("id", "unknown")
        count = 0

        # 할당 제약: sum == 1
        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for gk, gvars in groups.items():
                            label = f"{cid}_{gk}"
                            if category == "soft" and weight:
                                cqm.add_constraint(
                                    sum(gvars) == 1, label=label, weight=weight
                                )
                            else:
                                cqm.add_constraint(sum(gvars) == 1, label=label)
                            count += 1
                        break

        # 용량 제약: sum <= N
        elif "sum" in expr and "<=" in expr:
            import re
            nums = re.findall(r'<=\s*(\d+)', expr)
            ub = int(nums[0]) if nums else 10
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[-1], []).append(var)
                        for gk, gvars in groups.items():
                            label = f"{cid}_{gk}"
                            cqm.add_constraint(sum(gvars) <= ub, label=label)
                            count += 1
                        break

        return count

    def _parse_objective_cqm(self, cqm, var_map, obj_def, bound_data) -> bool:
        """CQM 목적함수 설정"""
        obj_type = obj_def.get("type", "minimize")

        all_vars = []
        for vid, v in var_map.items():
            if isinstance(v, dict):
                all_vars.extend(v.values())
            else:
                all_vars.append(v)

        if not all_vars:
            return False

        objective = sum(all_vars)
        if obj_type == "maximize":
            objective = -objective

        cqm.set_objective(objective)
        return True
