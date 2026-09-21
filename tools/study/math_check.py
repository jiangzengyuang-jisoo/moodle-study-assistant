"""Exact arithmetic checks, not automatic grading of proofs or course work."""
import ast
import json
import sys

import sympy as sp

FUNCTIONS = {name: getattr(sp, name) for name in (
    "sin", "cos", "tan", "asin", "acos", "atan", "exp", "log", "sqrt", "Abs", "conjugate", "re", "im")}
CONSTANTS = {"pi": sp.pi, "E": sp.E, "I": sp.I, "oo": sp.oo}
ASSUMPTIONS = {"real", "positive", "nonzero", "complex"}


def parse(text, symbols):
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError("表达式必须是最多 1000 字符的字符串")
    tree = ast.parse(text, mode="eval")
    if len(list(ast.walk(tree))) > 120:
        raise ValueError("表达式过长，请分步骤核验")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return sp.Rational(ast.get_source_segment(text, node))
        if isinstance(node, ast.Name) and node.id.isalpha() and node.id.isascii():
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            if node.id not in symbols:
                symbols[node.id] = sp.Symbol(node.id, real=True)
            return symbols[node.id]
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.USub):
                return sp.Mul(-1, value, evaluate=False)
            if isinstance(node.op, ast.UAdd):
                return value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return sp.Add(left, right, evaluate=False)
            if isinstance(node.op, ast.Sub):
                return sp.Add(left, sp.Mul(-1, right, evaluate=False), evaluate=False)
            if isinstance(node.op, ast.Mult):
                return sp.Mul(left, right, evaluate=False)
            if isinstance(node.op, ast.Div):
                return sp.Mul(left, sp.Pow(right, -1, evaluate=False), evaluate=False)
            if isinstance(node.op, ast.Pow):
                if not right.free_symbols and (right.is_real is not True or abs(right) > 1000):
                    raise ValueError("指数过大或不在简易核验范围内")
                return sp.Pow(left, right, evaluate=False)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in FUNCTIONS and not node.keywords and len(node.args) == 1):
            return FUNCTIONS[node.func.id](visit(node.args[0]), evaluate=False)
        raise ValueError("仅支持数学符号、四则运算、** 幂和白名单函数；不执行代码")
    return visit(tree.body)


def check(request):
    symbols = {}
    for name, assumption in request.get("symbols", {}).items():
        if not name.isascii() or not name.isalpha() or name in CONSTANTS or assumption not in ASSUMPTIONS:
            raise ValueError("符号假设使用 real/positive/nonzero/complex；常量不可重定义")
        symbols[name] = sp.Symbol(name, **{assumption: True})
    expr = parse(request["expression"], symbols)
    operation = request["operation"]
    output = {"operation": operation, "input": request["expression"], "status": "computed",
              "scope": "只核验指定计算；原题定义域、适用条件、证明步骤与老师格式须另行检查"}
    if operation == "equivalent":
        other = parse(request["other"], symbols)
        difference = sp.simplify(expr - other)
        equal = difference.equals(0)
        output.update(equal_on_common_domain=equal, same_domain_verified=False,
                      simplified_difference=str(difference), status="checked" if equal is not None else "unresolved")
        if any(sp.simplify(value).has(sp.nan, sp.zoo) for value in (expr, other)):
            output.update(equal_on_common_domain=None, status="undefined")
    elif operation == "limit":
        variable = parse(request.get("variable", "x"), symbols)
        if not isinstance(variable, sp.Symbol):
            raise ValueError("求极限变量必须是单个符号")
        point = sp.simplify(parse(request["point"], symbols))
        direction = request.get("direction", "both")
        if direction not in {"both", "+", "-"}:
            raise ValueError("方向使用 both、+（右）或 -（左）")
        output["original_value_checked"] = False
        if direction == "both" and point not in (sp.oo, -sp.oo):
            left, right = sp.limit(expr, variable, point, dir="-"), sp.limit(expr, variable, point, dir="+")
            output.update(left=str(left), right=str(right))
            if left.has(sp.Limit) or right.has(sp.Limit):
                output["status"] = "unresolved"
            elif left != right:
                output["status"] = "does_not_exist" if left.equals(right) is False else "unresolved"
            else:
                result_output(output, left)
        else:
            result_output(output, sp.limit(expr, variable, point, dir="+" if direction == "both" else direction))
    else:
        if operation == "simplify":
            result = sp.simplify(expr)
        elif operation == "evaluate":
            values = {parse(key, symbols): parse(value, symbols) for key, value in request.get("values", {}).items()}
            if any(not isinstance(key, sp.Symbol) for key in values):
                raise ValueError("代入值的键必须是符号")
            result = sp.simplify(expr.subs(values, simultaneous=True))
        elif operation in {"differentiate", "integrate"}:
            variable = parse(request.get("variable", "x"), symbols)
            if not isinstance(variable, sp.Symbol):
                raise ValueError("微积分变量必须是单个符号")
            result = sp.diff(expr, variable) if operation == "differentiate" else sp.integrate(expr, variable)
            if operation == "integrate":
                output["integration_constant"] = "不定积分还需 +C，并核对适用区间"
        else:
            raise ValueError("不支持该计算类型")
        result_output(output, result)
    output["symbol_assumptions"] = {str(s): "complex" if s.is_real is None else
                                    "positive" if s.is_positive else "nonzero" if s.is_nonzero else "real"
                                    for s in symbols.values()}
    return output


def result_output(output, result):
    output.update(result=str(result), latex=sp.latex(result))
    if result.has(sp.nan, sp.zoo):
        output["status"] = "undefined"
    elif result.has(sp.AccumBounds):
        output["status"] = "does_not_exist" if output["operation"] == "limit" else "unresolved"
    elif result.has(sp.Limit, sp.Integral, sp.Derivative):
        output["status"] = "unresolved"
    elif result in (sp.oo, -sp.oo):
        output["status"] = "infinite"


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(check(json.load(sys.stdin)), ensure_ascii=False))
    except (ValueError, TypeError, KeyError, SyntaxError, NotImplementedError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
