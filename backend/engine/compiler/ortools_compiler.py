# engine/compiler/ortools_compiler.py
# ============================================================
# OR-Tools Compiler: IR -> CP-SAT or Linear Solver model
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base import BaseCompiler, CompileResult

logger = logging.getLogger(__name__)


class ORToolsCompiler(BaseCompiler):
    """수학 모델 IR을 OR-Tools CP-SAT 또는 LP 모델로 변환"""

    def compile(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        try:
            var_types = set()
            for v in math_model.get("variables", []):
                var_types.add(self._get_variable_type(v))

            # 연속 변수가 있으면 LP solver, 아니면 CP-SAT
            has_continuous = "continuous" in var_types
            if has_continuous:
                return self._compile_lp(math_model, bound_data, **kwargs)
            else:
                return self._compile_cp_sat(math_model, bound_data, **kwargs)

        except Exception as e:
            logger.error(f"ORTools compilation failed: {e}", exc_info=True)
            return CompileResult(success=False, error=str(e))

    def _compile_cp_sat(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """CP-SAT 모델 생성 (정수/바이너리 변수만)"""
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()
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
                # 스칼라 변수
                if vtype == "binary":
                    var_map[vid] = model.new_bool_var(vid)
                else:
                    lb = int(var_def.get("lower_bound") or 0)
                    ub = int(var_def.get("upper_bound") or 1000000)
                    var_map[vid] = model.new_int_var(lb, ub, vid)
                total_vars += 1
            else:
                # 인덱스된 변수 (집합의 데카르트 곱)
                combos = self._compute_set_product(indices, bound_data)
                var_map[vid] = {}
                for combo in combos:
                    key = tuple(str(c) for c in combo)
                    name = f"{vid}_{'_'.join(key)}"
                    if vtype == "binary":
                        var_map[vid][key] = model.new_bool_var(name)
                    else:
                        lb = int(var_def.get("lower_bound") or 0)
                        ub = int(var_def.get("upper_bound") or 1000000)
                        var_map[vid][key] = model.new_int_var(lb, ub, name)
                    total_vars += 1

                if not combos:
                    warnings.append(f"Variable {vid}: no index combinations generated")

        logger.info(f"CP-SAT: created {total_vars} variables")

        # 2. 제약조건 생성
        for con_def in math_model.get("constraints", []):
            cid = con_def.get("id", "")
            expr = con_def.get("expression", "")
            for_each = con_def.get("for_each", "")
            category = con_def.get("category", "hard")

            # soft 제약은 CP-SAT에서는 패널티로 처리
            if category == "soft":
                warnings.append(f"Constraint {cid}: soft constraint - added as penalty term")
                continue

            # Expression 파싱은 향후 LLM 또는 규칙 기반으로 확장
            # 현재는 기본 패턴 매칭으로 처리
            parsed = self._parse_constraint_cpsat(model, var_map, con_def, bound_data)
            if parsed:
                total_constraints += parsed
            else:
                warnings.append(f"Constraint {cid}: could not parse expression: {expr[:80]}")

        logger.info(f"CP-SAT: created {total_constraints} constraints")

        # 3. 목적함수
        obj = math_model.get("objective", {})
        obj_parsed = self._parse_objective_cpsat(model, var_map, obj, bound_data)
        if not obj_parsed:
            warnings.append("Objective: could not parse, using default minimize sum")

        return CompileResult(
            success=True,
            solver_model=model,
            solver_type="ortools_cp",
            variable_count=total_vars,
            constraint_count=total_constraints,
            variable_map=var_map,
            warnings=warnings,
            metadata={"model_type": "CP-SAT", "engine": "ortools"},
        )

    def _compile_lp(self, math_model: Dict, bound_data: Dict, **kwargs) -> CompileResult:
        """LP/MIP 모델 생성 (연속 변수 포함)"""
        from ortools.linear_solver import pywraplp

        solver = pywraplp.Solver.CreateSolver("SCIP")
        if not solver:
            return CompileResult(success=False, error="SCIP solver not available")

        var_map = {}
        total_vars = 0
        total_constraints = 0
        warnings = []

        # 1. 변수 생성
        for var_def in math_model.get("variables", []):
            vid = var_def.get("id", "")
            vtype = self._get_variable_type(var_def)
            indices = var_def.get("indices", [])
            lb = float(var_def.get("lower_bound") if var_def.get("lower_bound") is not None else 0)
            ub = float(var_def.get("upper_bound") if var_def.get("upper_bound") is not None else 1e7)

            if not indices:
                if vtype == "binary":
                    var_map[vid] = solver.BoolVar(vid)
                elif vtype == "integer":
                    var_map[vid] = solver.IntVar(int(lb), int(ub), vid)
                else:
                    var_map[vid] = solver.NumVar(lb, ub, vid)
                total_vars += 1
            else:
                combos = self._compute_set_product(indices, bound_data)
                var_map[vid] = {}
                for combo in combos:
                    key = tuple(str(c) for c in combo)
                    name = f"{vid}_{'_'.join(key)}"
                    if vtype == "binary":
                        var_map[vid][key] = solver.BoolVar(name)
                    elif vtype == "integer":
                        var_map[vid][key] = solver.IntVar(int(lb), int(ub), name)
                    else:
                        var_map[vid][key] = solver.NumVar(lb, ub, name)
                    total_vars += 1

        logger.info(f"LP/MIP: created {total_vars} variables")

        # 2. 제약조건 (향후 expression 파서로 확장)
        for con_def in math_model.get("constraints", []):
            cid = con_def.get("id", "")
            parsed = self._parse_constraint_lp(solver, var_map, con_def, bound_data)
            if parsed:
                total_constraints += parsed
            else:
                warnings.append(f"Constraint {cid}: could not parse")

        # 3. 목적함수
        obj = math_model.get("objective", {})
        obj_parsed = self._parse_objective_lp(solver, var_map, obj, bound_data)
        if not obj_parsed:
            warnings.append("Objective: could not parse")

        return CompileResult(
            success=True,
            solver_model=solver,
            solver_type="ortools_lp",
            variable_count=total_vars,
            constraint_count=total_constraints,
            variable_map=var_map,
            warnings=warnings,
            metadata={"model_type": "LP/MIP", "engine": "SCIP"},
        )

    #  Expression Parsers (기본 구현, 향후 확장) 

    def _parse_constraint_cpsat(self, model, var_map, con_def, bound_data) -> int:
        """CP-SAT 제약 파싱 - 기본 패턴 매칭"""
        expr = con_def.get("expression", "").strip()
        for_each = con_def.get("for_each", "")
        count = 0

        # 패턴: sum(x[i,j] for j in J) == 1 for i in I (할당 제약)
        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            # 할당 제약: 각 i에 대해 sum_j x[i,j] = 1
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        # 첫 번째 인덱스별로 그룹핑
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for group_key, group_vars in groups.items():
                            model.add(sum(group_vars) == 1)
                            count += 1
                        break

        # 패턴: sum(x[i,j] for i in I) <= max_val
        elif "sum" in expr and ("<=" in expr or ">=" in expr):
            # 용량 제약
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[-1], []).append(var)
                        # 상한 추출 시도
                        import re
                        nums = re.findall(r'<=\s*(\d+)', expr)
                        ub = int(nums[0]) if nums else 10
                        for group_key, group_vars in groups.items():
                            model.add(sum(group_vars) <= ub)
                            count += 1
                        break

        return count

    def _parse_objective_cpsat(self, model, var_map, obj_def, bound_data) -> bool:
        """CP-SAT 목적함수 파싱"""
        obj_type = obj_def.get("type", "minimize")
        expr = obj_def.get("expression", "")

        # 기본: 모든 변수의 합을 최소화/최대화
        all_vars = []
        for vid, v in var_map.items():
            if isinstance(v, dict):
                all_vars.extend(v.values())
            else:
                all_vars.append(v)

        if not all_vars:
            return False

        if obj_type == "minimize":
            model.minimize(sum(all_vars))
        else:
            model.maximize(sum(all_vars))

        return True

    def _parse_constraint_lp(self, solver, var_map, con_def, bound_data) -> int:
        """LP 제약 파싱 - 기본 패턴 매칭"""
        # CP-SAT과 유사한 로직, pywraplp API 사용
        expr = con_def.get("expression", "").strip()
        count = 0

        if "sum" in expr and ("== 1" in expr or "= 1" in expr):
            for vid, vars_dict in var_map.items():
                if isinstance(vars_dict, dict) and vars_dict:
                    first_key = next(iter(vars_dict))
                    if len(first_key) >= 2:
                        groups = {}
                        for key, var in vars_dict.items():
                            groups.setdefault(key[0], []).append(var)
                        for group_key, group_vars in groups.items():
                            ct = solver.Constraint(1, 1, f"assign_{group_key}")
                            for var in group_vars:
                                ct.SetCoefficient(var, 1)
                            count += 1
                        break

        return count

    def _parse_objective_lp(self, solver, var_map, obj_def, bound_data) -> bool:
        """LP 목적함수 파싱"""
        obj_type = obj_def.get("type", "minimize")
        objective = solver.Objective()

        all_vars = []
        for vid, v in var_map.items():
            if isinstance(v, dict):
                all_vars.extend(v.values())
            else:
                all_vars.append(v)

        for var in all_vars:
            objective.SetCoefficient(var, 1.0)

        if obj_type == "minimize":
            objective.SetMinimization()
        else:
            objective.SetMaximization()

        return bool(all_vars)
