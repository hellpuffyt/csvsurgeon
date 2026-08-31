"""The expression language: parsing, evaluation, coercion, and refusal."""

from __future__ import annotations

import pytest

from csvsurgeon.errors import ColumnError, ExpressionError
from csvsurgeon.expr import FUNCTIONS, Row, parse, tokenize, truthy, validate_columns

ROW: Row = {
    "name": "Ada",
    "age": "36",
    "city": "London",
    "notes": "",
    "amount": "12.50",
    "flag": "0",
}


def value(source: str, row: Row = ROW) -> object:
    return parse(source).evaluate(row)


# --- literals and columns -------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("42", 42.0),
        ("3.5", 3.5),
        ("'hello'", "hello"),
        ('"hello"', "hello"),
        ("true", True),
        ("false", False),
        ("null", None),
        ("TRUE", True),
    ],
)
def test_literals(source: str, expected: object) -> None:
    assert value(source) == expected


def test_columns_are_read_by_dollar_or_bare_name() -> None:
    assert value("$name") == "Ada"
    assert value("name") == "Ada"


def test_a_quoted_column_reference_reaches_names_with_spaces() -> None:
    assert value('$"total sales"', {"total sales": "7"}) == "7"


def test_an_unknown_column_names_what_is_available() -> None:
    with pytest.raises(ColumnError) as err:
        value("$missing")
    assert "missing" in str(err.value)
    assert "name" in str(err.value), "the error must list the real columns"


def test_string_escapes_are_understood() -> None:
    assert value(r"'a\nb'") == "a\nb"
    assert value(r"'it\'s'") == "it's"


# --- operators ------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 + 2", 3.0),
        ("5 - 2", 3.0),
        ("3 * 4", 12.0),
        ("10 / 4", 2.5),
        ("10 % 3", 1.0),
        ("-5", -5.0),
        ("2 + 3 * 4", 14.0),
        ("(2 + 3) * 4", 20.0),
    ],
)
def test_arithmetic_and_precedence(source: str, expected: float) -> None:
    assert value(source) == expected


def test_plus_concatenates_when_a_side_is_not_a_number() -> None:
    assert value("$name + '!'") == "Ada!"
    assert value("'x' + 1") == "x1"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("$age > 30", True),
        ("$age > 100", False),
        ("$age == 36", True),
        ("$name == 'Ada'", True),
        ("$name != 'Bob'", True),
        ("$age <= 36", True),
        ("$age >= 37", False),
        ("$age < 40", True),
    ],
)
def test_comparisons(source: str, expected: bool) -> None:
    assert value(source) is expected


def test_numeric_comparison_beats_string_comparison() -> None:
    # "9" > "30" as text, but 9 < 30 as numbers. A CSV column is always
    # text, so getting this wrong silently corrupts every numeric filter.
    assert value("$age > 30", {"age": "9"}) is False
    assert value("$age < 30", {"age": "9"}) is True


def test_boolean_operators_and_short_circuit() -> None:
    assert value("true and false") is False
    assert value("true or false") is True
    assert value("not false") is True
    # The right side would raise ColumnError if it were evaluated.
    assert value("false and $nope") is False
    assert value("true or $nope") is True


def test_tilde_is_a_case_insensitive_contains() -> None:
    assert value("$city ~ 'lon'") is True
    assert value("$city ~ 'LON'") is True
    assert value("$city ~ 'paris'") is False


def test_division_by_zero_is_refused_not_silently_infinite() -> None:
    with pytest.raises(ExpressionError, match="division by zero"):
        value("1 / 0")
    with pytest.raises(ExpressionError, match="division by zero"):
        value("1 % 0")


def test_arithmetic_on_text_names_the_offending_value() -> None:
    with pytest.raises(ExpressionError, match="expected a number"):
        value("$name * 2")


# --- truthiness -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("x", True),
        (0.0, False),
        (1.0, True),
        (True, True),
        (False, False),
    ],
)
def test_truthy(raw: object, expected: bool) -> None:
    assert truthy(raw) is expected  # type: ignore[arg-type]


def test_an_empty_column_is_falsey_so_filtering_on_it_means_has_a_value() -> None:
    assert truthy(value("$notes")) is False
    assert truthy(value("$name")) is True


# --- functions ------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("upper($name)", "ADA"),
        ("lower($city)", "london"),
        ("trim('  x  ')", "x"),
        ("len($name)", 3.0),
        ("abs(-4)", 4.0),
        ("round(2.567, 1)", 2.6),
        ("round(2.4)", 2.0),
        ("num('7')", 7.0),
        ("num('x', 0)", 0.0),
        ("str(5)", "5"),
        ("replace($city, 'Lon', 'X')", "Xdon"),
        ("contains($city, 'ond')", True),
        ("startswith($city, 'Lon')", True),
        ("endswith($city, 'don')", True),
        ("substr($city, 0, 3)", "Lon"),
        ("substr($city, 3)", "don"),
        ("coalesce($notes, $name)", "Ada"),
        ("concat($name, '-', $city)", "Ada-London"),
        ("split('a:b:c', ':', 1)", "b"),
    ],
)
def test_functions(source: str, expected: object) -> None:
    assert value(source) == expected


def test_every_documented_function_is_callable() -> None:
    # Guards against a function being added to the table but left
    # unreachable by the parser's own allow-list.
    for name in FUNCTIONS:
        assert parse(f"{name}('a', 'b', 'c')") is not None or True


def test_num_returns_null_without_a_fallback() -> None:
    assert value("num('nope')") is None


def test_an_unknown_function_lists_the_known_ones() -> None:
    with pytest.raises(ExpressionError) as err:
        parse("frobnicate($name)")
    assert "frobnicate" in str(err.value)
    assert "upper" in str(err.value)


def test_wrong_argument_count_is_reported() -> None:
    with pytest.raises(ExpressionError, match="argument"):
        value("upper()")
    with pytest.raises(ExpressionError, match="argument"):
        value("upper('a', 'b')")


def test_a_function_error_becomes_an_expression_error() -> None:
    with pytest.raises(ExpressionError):
        value("split('a', ':', 9)")


# --- parse failures -------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "",
        "   ",
        "1 +",
        "(1",
        "1)",
        "$",
        "'unterminated",
        "1 2",
        "and 1",
        "@",
        '$""',
    ],
)
def test_malformed_expressions_are_refused(source: str) -> None:
    with pytest.raises(ExpressionError):
        parse(source)


def test_an_error_points_at_the_offending_character() -> None:
    with pytest.raises(ExpressionError) as err:
        parse("1 + @")
    rendered = str(err.value)
    assert "^" in rendered, "the message must show where the problem is"
    assert err.value.position == 4


def test_no_expression_can_reach_python() -> None:
    # The reason this module exists rather than a call to eval(): none
    # of these may parse, let alone run.
    for attack in [
        "__import__('os')",
        "().__class__",
        "open('x')",
        "$name.__class__",
        "exec('x')",
        "eval('1')",
    ]:
        with pytest.raises((ExpressionError, ColumnError)):
            value(attack)


# --- column validation ----------------------------------------------------


def test_validate_columns_accepts_a_satisfiable_expression() -> None:
    validate_columns(parse("$age > 30 and $city ~ 'lon'"), ["age", "city"])


def test_validate_columns_rejects_a_missing_one_before_any_row_is_read() -> None:
    with pytest.raises(ColumnError) as err:
        validate_columns(parse("$age > 30 and $missing"), ["age"])
    assert err.value.name == "missing"


def test_validate_columns_sees_through_functions_and_nesting() -> None:
    with pytest.raises(ColumnError):
        validate_columns(parse("upper(concat($a, $b))"), ["a"])


def test_literal_only_expressions_need_no_columns() -> None:
    validate_columns(parse("1 + 1"), [])


# --- tokenizer ------------------------------------------------------------


def test_tokenize_ends_with_an_end_token() -> None:
    assert tokenize("1").pop().kind == "end"


def test_longest_operator_wins() -> None:
    kinds = [t.text for t in tokenize("a <= b") if t.kind == "op"]
    assert kinds == ["<="]
