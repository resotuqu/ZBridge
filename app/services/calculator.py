from __future__ import annotations

import ast
import operator
import re
from decimal import (
    Decimal,
    DivisionByZero,
    InvalidOperation,
    ROUND_HALF_UP,
)


_MAX_EXPRESSION_LENGTH = 200
_MAX_AST_NODES = 100

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")

_BINARY_OPERATORS: dict[type, object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_UNARY_OPERATORS: dict[type, object] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculatorError(Exception):
    pass


class EmptyExpressionError(CalculatorError):
    pass


class InvalidExpressionError(CalculatorError):
    pass


class DivisionByZeroCalcError(CalculatorError):
    pass


def evaluate(expression: str) -> Decimal:
    """
    Safely evaluate a basic arithmetic expression.

    Supports +, -, *, /, parentheses, unary +/-, and a
    postfix "%" on numeric literals (treated as division
    by 100, e.g. "50%" -> 0.5). No name/attribute/call/
    subscript access is possible: the AST is walked with
    a strict node whitelist; the text is never run through
    the eval, exec, or compile builtins.
    """
    stripped = expression.strip()

    if not stripped:
        raise EmptyExpressionError("Expression is empty.")

    if len(stripped) > _MAX_EXPRESSION_LENGTH:
        raise InvalidExpressionError(
            "Expression is too long."
        )

    transformed = _PERCENT_RE.sub(r"(\1/100)", stripped)

    try:
        tree = ast.parse(transformed, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise InvalidExpressionError(
            "Expression could not be parsed."
        ) from exc

    node_count = sum(1 for _ in ast.walk(tree))

    if node_count > _MAX_AST_NODES:
        raise InvalidExpressionError(
            "Expression is too complex."
        )

    try:
        result = _eval_node(tree.body)
    except (DivisionByZero, ZeroDivisionError) as exc:
        raise DivisionByZeroCalcError(
            "Division by zero."
        ) from exc
    except CalculatorError:
        raise
    except (
        TypeError,
        ValueError,
        OverflowError,
        InvalidOperation,
    ) as exc:
        raise InvalidExpressionError(
            "Expression could not be evaluated."
        ) from exc

    if not result.is_finite():
        raise InvalidExpressionError(
            "Expression result is not finite."
        )

    return result


def _eval_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(
            node.value, bool
        ) or not isinstance(
            node.value, (int, float)
        ):
            raise InvalidExpressionError(
                "Unsupported constant."
            )

        return Decimal(str(node.value))

    if isinstance(node, ast.BinOp):
        op = _BINARY_OPERATORS.get(type(node.op))

        if op is None:
            raise InvalidExpressionError(
                "Unsupported operator."
            )

        left = _eval_node(node.left)
        right = _eval_node(node.right)

        if op is operator.truediv and right == 0:
            raise DivisionByZeroCalcError(
                "Division by zero."
            )

        return op(left, right)  # type: ignore[operator]

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPERATORS.get(type(node.op))

        if op is None:
            raise InvalidExpressionError(
                "Unsupported operator."
            )

        return op(_eval_node(node.operand))  # type: ignore[operator]

    raise InvalidExpressionError(
        "Unsupported expression."
    )


def format_result(value: Decimal) -> str:
    quantized = value.quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )

    text = format(quantized, "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"
