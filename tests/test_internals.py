"""The edges: coercion corners, arity reporting, and large-sample reading.

These are the paths the feature tests do not reach on their own — mostly
branches that only fire on unusual input. They are exactly the branches
that produce wrong data rather than an error when they are wrong, so
they are worth pinning down explicitly.
"""

from __future__ import annotations

import io

import pytest

from csvsurgeon.errors import ExpressionError, UsageError
from csvsurgeon.expr import FUNCTIONS, Function, _as_text, _to_number, parse
from csvsurgeon.io import SNIFF_BYTES, _chain_lines, read_rows

# --- text rendering -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "true"),
        (False, "false"),
        (3.0, "3"),
        (3.5, "3.5"),
        ("x", "x"),
    ],
)
def test_as_text(value: object, expected: str) -> None:
    assert _as_text(value) == expected  # type: ignore[arg-type]


def test_a_whole_float_renders_without_a_decimal_point() -> None:
    """A computed column must match the input it copied.

    `$age` of "36" times 1 is 36.0 internally; writing "36.0" would make
    the output silently differ from the file it came from.
    """
    assert _as_text(36.0) == "36"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, 1.0),
        (False, 0.0),
        ("", None),
        ("  ", None),
        ("7", 7.0),
        ("nope", None),
        (2.5, 2.5),
    ],
)
def test_to_number(value: object, expected: float | None) -> None:
    assert _to_number(value) == expected  # type: ignore[arg-type]


def test_a_boolean_compares_as_a_number() -> None:
    assert parse("true == 1").evaluate({}) is True


# --- columns through every node type --------------------------------------


def test_a_negation_reports_the_columns_it_reads() -> None:
    assert parse("not $flag").columns() == {"flag"}


def test_a_unary_minus_reports_the_columns_it_reads() -> None:
    assert parse("-$amount").columns() == {"amount"}


# --- arity reporting ------------------------------------------------------


def test_arity_text_covers_fixed_ranged_and_open_ended_functions() -> None:
    assert "1 argument" in Function(lambda a: a[0], 1, 1).arity_text()
    assert "2 to 3" in Function(lambda a: a[0], 2, 3).arity_text()
    assert "at least 1" in Function(lambda a: a[0], 1, None).arity_text()


def test_an_open_ended_function_accepts_many_arguments() -> None:
    assert FUNCTIONS["concat"].accepts(9)
    assert not FUNCTIONS["upper"].accepts(2)


def test_an_expression_error_raised_inside_a_function_is_not_reclassified() -> None:
    # `substr` asks for a number; the ExpressionError that produces must
    # propagate, not be swallowed and re-wrapped as something vaguer.
    with pytest.raises(ExpressionError, match="expected a number"):
        parse("substr('abc', 'x')").evaluate({})


def test_coalesce_of_nothing_truthy_is_empty() -> None:
    assert parse("coalesce('', '  ', null)").evaluate({}) == ""


# --- reading past the sniff sample ----------------------------------------


def test_a_file_larger_than_the_sniff_sample_reads_completely() -> None:
    """The sample boundary must not eat or split a row.

    The sniff reads a fixed number of characters, which lands in the
    middle of a line on any real file. Every row after that boundary
    depends on the tail being rejoined correctly.
    """
    rows = 5000
    text = "name,age\n" + "".join(f"person{i},{i}\n" for i in range(rows))
    assert len(text) > SNIFF_BYTES, "the sample must not cover the whole file"

    header, stream = read_rows(io.StringIO(text))
    got = list(stream)

    assert header == ["name", "age"]
    assert len(got) == rows, "no row may be lost at the sample boundary"
    assert got[0]["name"] == "person0"
    assert got[-1] == {"name": f"person{rows - 1}", "age": str(rows - 1)}


def test_chain_lines_rejoins_a_sample_that_ends_mid_line() -> None:
    rest = io.StringIO("cde\nfgh\n")
    assert list(_chain_lines("ab\nab", rest)) == ["ab\n", "abcde\n", "fgh\n"]


def test_chain_lines_handles_a_sample_that_ends_on_a_boundary() -> None:
    rest = io.StringIO("cde\n")
    assert list(_chain_lines("ab\n", rest)) == ["ab\n", "cde\n"]


def test_chain_lines_with_nothing_after_the_sample() -> None:
    assert list(_chain_lines("ab\ncd", io.StringIO(""))) == ["ab\n", "cd"]


# --- empty input on the explicit-delimiter path ---------------------------


def test_empty_input_with_an_explicit_delimiter_is_still_a_usage_error() -> None:
    # The sniffing path rejects empty input before the reader is built;
    # this path only finds out when the reader yields nothing.
    with pytest.raises(UsageError, match="empty"):
        read_rows(io.StringIO(""), delimiter=",")
