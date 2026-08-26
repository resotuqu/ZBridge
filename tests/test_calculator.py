from decimal import Decimal

import pytest

from app.services.calculator import (
    DivisionByZeroCalcError,
    EmptyExpressionError,
    InvalidExpressionError,
    evaluate,
    format_result,
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+1", Decimal("2")),
        ("2-5", Decimal("-3")),
        ("3*4", Decimal("12")),
        ("10/4", Decimal("2.5")),
        ("1250*1.2", Decimal("1500")),
        ("(10+5)*2", Decimal("30")),
        ("2*(3+(4-1))", Decimal("12")),
        ("-5+3", Decimal("-2")),
        ("+5", Decimal("5")),
        ("--5", Decimal("5")),
        ("2+3*4", Decimal("14")),
        ("(2+3)*4", Decimal("20")),
    ],
)
def test_evaluate_basic_arithmetic(
    expression: str, expected: Decimal
) -> None:
    assert evaluate(expression) == expected


def test_evaluate_respects_operator_precedence() -> None:
    assert evaluate("2+3*4-1") == Decimal("13")
    assert evaluate("10-2*3") == Decimal("4")


def test_evaluate_percent_of_literal() -> None:
    assert evaluate("50%") == Decimal("0.5")
    assert evaluate("10%*200") == Decimal("20")
    assert evaluate("1250*1.2%") == Decimal("15")


def test_evaluate_division_by_zero_raises() -> None:
    with pytest.raises(DivisionByZeroCalcError):
        evaluate("10/0")


def test_evaluate_division_by_zero_expression_raises() -> None:
    with pytest.raises(DivisionByZeroCalcError):
        evaluate("5/(2-2)")


def test_evaluate_empty_expression_raises() -> None:
    with pytest.raises(EmptyExpressionError):
        evaluate("")

    with pytest.raises(EmptyExpressionError):
        evaluate("   ")


@pytest.mark.parametrize(
    "expression",
    [
        "2 ** 3",
        "__import__('os')",
        "os.system('ls')",
        "1; 2",
        "[1, 2, 3]",
        "{1: 2}",
        "lambda: 1",
        "1 if True else 2",
        "1 < 2",
        "1 and 2",
        "abc",
        "print(1)",
        "1 // 2",
        "2 % 3",
        "1 + ",
        "1 +* 2",
        "(1+2",
    ],
)
def test_evaluate_rejects_unsupported_or_invalid_input(
    expression: str,
) -> None:
    with pytest.raises(InvalidExpressionError):
        evaluate(expression)


def test_evaluate_rejects_too_long_expression() -> None:
    with pytest.raises(InvalidExpressionError):
        evaluate("1+" * 500 + "1")


def test_evaluate_rejects_too_complex_expression() -> None:
    # 80 additions stay under the 200-char length limit
    # but exceed the AST node-count limit (100).
    expression = "+".join(["1"] * 80)
    assert len(expression) < 200

    with pytest.raises(InvalidExpressionError):
        evaluate(expression)


def test_calculator_source_never_calls_eval_or_exec() -> None:
    import inspect

    from app.services import calculator

    source = inspect.getsource(calculator)

    # ast.parse() is the sanctioned safe parser (it only
    # ever builds a syntax tree, via compile(..., ast.PyCF_ONLY_AST),
    # and never executes anything); direct eval()/exec() calls on
    # user input are what must never appear.
    assert "eval(" not in source
    assert "exec(" not in source
    assert "__import__" not in source


def test_evaluate_rejects_dunder_attribute_access() -> None:
    with pytest.raises(InvalidExpressionError):
        evaluate("().__class__")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("2"), "2"),
        (Decimal("1500.00"), "1500"),
        (Decimal("2.5"), "2.5"),
        (Decimal("0.333333"), "0.333333"),
        (Decimal("-3"), "-3"),
        (Decimal("0"), "0"),
    ],
)
def test_format_result(
    value: Decimal, expected: str
) -> None:
    assert format_result(value) == expected
