"""Deterministic arithmetic evaluator.

The calculator walks a small allow-listed subset of Python's AST. It never calls eval,
never imports modules from user input, and never touches the filesystem or network.
"""

import ast
import math
from typing import Any

from app.tools.base import ToolError

MAX_EXPRESSION_CHARS = 500
MAX_ABS_RESULT = 1e100
MAX_POWER_EXPONENT = 400
MAX_FACTORIAL = 170

_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left ** right,
}

_UNARY = {
    ast.UAdd: lambda value: +value,
    ast.USub: lambda value: -value,
}

_ALLOWED_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _factorial(value: float) -> int:
    if not float(value).is_integer() or value < 0 or value > MAX_FACTORIAL:
        raise ToolError(f"factorial accepts whole numbers from 0 to {MAX_FACTORIAL}.")
    return math.factorial(int(value))


def _log(value: float, base: float = math.e) -> float:
    if value <= 0 or base <= 0 or base == 1:
        raise ToolError("logarithm arguments are outside the valid domain.")
    return math.log(value, base)


def _bounded_function(function):
    def wrapped(*args: float) -> float:
        result = function(*args)
        if isinstance(result, complex):
            raise ToolError("The expression produced a complex result.")
        if isinstance(result, (int, float)) and abs(result) > MAX_ABS_RESULT:
            raise ToolError("The result is too large to display.")
        return result

    return wrapped


def _round(value: float, digits: float = 0) -> float:
    if not float(digits).is_integer() or abs(digits) > 12:
        raise ToolError("round accepts digits between -12 and 12.")
    return round(value, int(digits))


_FUNCTIONS: dict[str, Any] = {
    "abs": _bounded_function(abs),
    "acos": _bounded_function(math.acos),
    "asin": _bounded_function(math.asin),
    "atan": _bounded_function(math.atan),
    "ceil": _bounded_function(math.ceil),
    "cos": _bounded_function(math.cos),
    "degrees": _bounded_function(math.degrees),
    "exp": _bounded_function(math.exp),
    "factorial": _factorial,
    "floor": _bounded_function(math.floor),
    "ln": _bounded_function(math.log),
    "log": _log,
    "log10": _bounded_function(math.log10),
    "max": _bounded_function(max),
    "min": _bounded_function(min),
    "pow": _bounded_function(math.pow),
    "radians": _bounded_function(math.radians),
    "round": _round,
    "sin": _bounded_function(math.sin),
    "sqrt": _bounded_function(math.sqrt),
    "tan": _bounded_function(math.tan),
}


def _evaluate(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError("Only numeric literals are supported.")
        if abs(node.value) > MAX_ABS_RESULT:
            raise ToolError("A numeric literal is too large.")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise ToolError(f"Unknown name '{node.id}'. Use numbers, operators, and supported functions.")
        return _ALLOWED_NAMES[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_POWER_EXPONENT:
                raise ToolError(f"Exponents are limited to {MAX_POWER_EXPONENT}.")
            if abs(left) > 1 and right > 0 and right * math.log10(abs(left)) > 100:
                raise ToolError("The result is too large to display.")
        try:
            result = _BINARY[type(node.op)](left, right)
        except ZeroDivisionError as error:
            raise ToolError("Division by zero is not defined.") from error
        except OverflowError as error:
            raise ToolError("The result is too large to display.") from error
        if isinstance(result, complex):
            raise ToolError("The expression produced a complex result.")
        if isinstance(result, (int, float)) and abs(result) > MAX_ABS_RESULT:
            raise ToolError("The result is too large to display.")
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = _FUNCTIONS.get(node.func.id)
        if function is None:
            raise ToolError(f"Unknown function '{node.func.id}'.")
        if node.keywords:
            raise ToolError("Keyword arguments are not supported.")
        arguments = [_evaluate(argument) for argument in node.args]
        try:
            return function(*arguments)
        except ToolError:
            raise
        except (TypeError, ValueError) as error:
            raise ToolError(f"{node.func.id} received an invalid argument.") from error
        except OverflowError as error:
            raise ToolError("The result is too large to display.") from error
        except ZeroDivisionError as error:
            raise ToolError("Division by zero is not defined.") from error
    raise ToolError("Unsupported expression. Use numbers, parentheses, arithmetic, and supported functions.")


def calculate(expression: str) -> dict[str, Any]:
    source = expression.strip()
    if not source:
        raise ToolError("Enter a calculation.")
    if len(source) > MAX_EXPRESSION_CHARS:
        raise ToolError(f"Expressions are limited to {MAX_EXPRESSION_CHARS} characters.")
    try:
        parsed = ast.parse(source, mode="eval")
    except SyntaxError as error:
        raise ToolError("The expression is not valid arithmetic syntax.") from error
    result = _evaluate(parsed)
    if isinstance(result, float):
        if not math.isfinite(result):
            raise ToolError("The result is not a finite number.")
        rendered = format(result, ".12g")
    else:
        rendered = str(result)
    return {"expression": source, "result": rendered, "value": result}
