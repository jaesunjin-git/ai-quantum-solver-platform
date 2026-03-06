with open('engine/compiler/ortools_compiler.py', encoding='utf-8') as f:
    content = f.read()

# 1) import 추가
old_import = 'from .struct_builder import BuildContext, build_constraint, build_constraints_batch, apply_constraint_cpsat, apply_constraint_lp, eval_node'
new_import = old_import + '\nfrom .expression_parser import parse_and_apply_expression'

if 'expression_parser' not in content:
    content = content.replace(old_import, new_import)
    print('[1] import expression_parser: OK')
else:
    print('[1] import already exists')

# 2) LP hard constraint 처리에서 expression 우선 사용
# 현재 흐름 (line 462~):
#   has_struct -> build_constraint -> apply_constraint_lp
#   fallback -> _parse_constraint_lp_legacy
#
# 새 흐름:
#   expression 있으면 -> parse_and_apply_expression (최우선)
#   has_struct -> build_constraint -> apply_constraint_lp
#   fallback -> _parse_constraint_lp_legacy

old_block = """            has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None
            parsed_count = 0

            if has_struct:"""

new_block = """            # (1) expression 문자열이 있으면 expression_parser 우선 사용
            expr_str = con_def.get("expression", "").strip()
            for_each_str = con_def.get("for_each", "")
            parsed_count = 0

            if expr_str and any(op in expr_str for op in ["<=", ">=", "=="]):
                try:
                    parsed_count = parse_and_apply_expression(
                        solver, expr_str, for_each_str, ctx, var_map
                    )
                    if parsed_count > 0:
                        total_constraints += parsed_count
                        logger.info(f"Constraint '{cname}': {parsed_count} instances (expression_parser)")
                        continue
                except Exception as e:
                    warnings.append(f"Constraint {cname}: expression_parser error ({e})")

            # (2) structured JSON (lhs/rhs) 처리
            has_struct = con_def.get("lhs") is not None and con_def.get("rhs") is not None

            if has_struct:"""

if 'expression_parser' not in content.split('has_struct')[0][-200:]:
    content = content.replace(old_block, new_block, 1)
    print('[2] expression_parser priority block: OK')
else:
    print('[2] block already modified')

with open('engine/compiler/ortools_compiler.py', 'w', encoding='utf-8') as f:
    f.write(content)

import py_compile
py_compile.compile('engine/compiler/ortools_compiler.py', doraise=True)
print('syntax: OK')
