"""Reading and writing: dialect sniffing, ragged rows, and stream safety."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from csvsurgeon.errors import UsageError
from csvsurgeon.io import (
    OVERFLOW_KEY,
    open_input,
    open_output,
    read_rows,
    sniff_dialect,
    write_rows,
)

CSV = "name,age\nAda,36\nBob,9\n"


def read(
    text: str, delimiter: str | None = None
) -> tuple[list[str], list[dict[str, str]]]:
    header, rows = read_rows(io.StringIO(text), delimiter)
    return header, [dict(r) for r in rows]


def test_a_plain_file_reads_header_and_rows() -> None:
    header, rows = read(CSV)
    assert header == ["name", "age"]
    assert rows == [{"name": "Ada", "age": "36"}, {"name": "Bob", "age": "9"}]


def test_the_sniffed_sample_is_not_swallowed() -> None:
    """The header must survive dialect sniffing.

    Sniffing reads from the stream; without pushing the sample back, the
    first row silently disappears — the kind of bug that only shows up
    on real files.
    """
    header, rows = read(CSV)
    assert header == ["name", "age"]
    assert len(rows) == 2, "no row may be consumed by the sniff"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_common_delimiters_are_detected(delimiter: str) -> None:
    text = f"name{delimiter}age\nAda{delimiter}36\nBob{delimiter}9\n"
    header, rows = read(text)
    assert header == ["name", "age"]
    assert rows[0]["age"] == "36"


def test_an_explicit_delimiter_overrides_sniffing() -> None:
    header, rows = read("name;age\nAda;36\n", delimiter=";")
    assert header == ["name", "age"]
    assert rows[0]["name"] == "Ada"


def test_a_single_column_file_falls_back_to_the_default_dialect() -> None:
    header, rows = read("name\nAda\nBob\n")
    assert header == ["name"]
    assert [r["name"] for r in rows] == ["Ada", "Bob"]


def test_quoted_fields_with_commas_and_newlines_survive() -> None:
    header, rows = read('name,note\n"Ada","a,b"\n"Bob","line1\nline2"\n')
    assert header == ["name", "note"]
    assert rows[0]["note"] == "a,b"
    assert rows[1]["note"] == "line1\nline2"


def test_short_rows_are_padded_rather_than_crashing() -> None:
    _, rows = read("name,age,city\nAda,36\n", delimiter=",")
    assert rows[0] == {"name": "Ada", "age": "36", "city": ""}


def test_long_rows_keep_their_overflow_visibly() -> None:
    _, rows = read("name,age\nAda,36,extra,more\n", delimiter=",")
    assert rows[0]["name"] == "Ada"
    assert rows[0][OVERFLOW_KEY] == "extra,more"


def test_a_trailing_blank_line_is_not_a_row() -> None:
    _, rows = read("name,age\nAda,36\n\n", delimiter=",")
    assert len(rows) == 1


def test_header_only_input_yields_no_rows() -> None:
    header, rows = read("name,age\n", delimiter=",")
    assert header == ["name", "age"]
    assert rows == []


def test_header_names_are_stripped() -> None:
    header, _ = read(" name , age \nAda,36\n", delimiter=",")
    assert header == ["name", "age"]


@pytest.mark.parametrize("text", ["", "\n"])
def test_empty_input_is_a_usage_error_not_a_crash(text: str) -> None:
    with pytest.raises(UsageError, match="empty|blank"):
        read(text)


def test_a_blank_header_is_refused() -> None:
    with pytest.raises(UsageError, match="blank"):
        read(",,\nA,B,C\n", delimiter=",")


def test_sniff_dialect_falls_back_instead_of_raising() -> None:
    dialect = sniff_dialect("!!!not really csv!!!")
    assert dialect.delimiter == ","


# --- writing --------------------------------------------------------------


def test_write_rows_is_rectangular_and_counts_what_it_wrote() -> None:
    out = io.StringIO()
    written = write_rows(
        out,
        ["name", "age"],
        [{"name": "Ada", "age": "36"}, {"name": "Bob"}],
    )
    assert written == 2
    assert out.getvalue() == "name,age\nAda,36\nBob,\n"


def test_values_outside_the_header_are_dropped() -> None:
    out = io.StringIO()
    write_rows(out, ["name"], [{"name": "Ada", "secret": "x"}])
    assert "secret" not in out.getvalue()
    assert "x" not in out.getvalue()


def test_the_output_delimiter_is_honoured() -> None:
    out = io.StringIO()
    write_rows(out, ["a", "b"], [{"a": "1", "b": "2"}], delimiter=";")
    assert out.getvalue() == "a;b\n1;2\n"


def test_a_value_containing_the_delimiter_is_quoted_on_the_way_out() -> None:
    out = io.StringIO()
    write_rows(out, ["a"], [{"a": "x,y"}])
    assert out.getvalue() == 'a\n"x,y"\n'


def test_round_trip_preserves_content() -> None:
    header, rows = read(CSV)
    out = io.StringIO()
    write_rows(out, header, rows)
    assert out.getvalue() == CSV


# --- file handles ---------------------------------------------------------


def test_open_input_reads_a_real_file(tmp_path: Path) -> None:
    path = tmp_path / "in.csv"
    # newline="" on the way in and out: csv owns line endings, so the
    # bytes must survive without the platform translating them.
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(CSV)
    with open_input(str(path), "utf-8") as handle:
        assert handle.read() == CSV


def test_open_input_reports_a_missing_file_by_name() -> None:
    with pytest.raises(UsageError, match="no such file"), open_input(
        "definitely-not-here.csv", "utf-8"
    ):
        pass


def test_open_output_writes_a_real_file(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    with open_output(str(path), "utf-8") as handle:
        handle.write(CSV)
    with path.open("r", encoding="utf-8", newline="") as handle:
        assert handle.read() == CSV


def test_open_input_and_output_fall_back_to_the_supplied_streams() -> None:
    with open_input("-", "utf-8", io.StringIO(CSV)) as handle:
        assert handle.read() == CSV
    sink = io.StringIO()
    with open_output(None, "utf-8", sink) as handle:
        handle.write("x")
    assert sink.getvalue() == "x"


def test_open_output_reports_a_missing_directory() -> None:
    with pytest.raises(UsageError, match="no such directory"), open_output(
        "no/such/dir/out.csv", "utf-8"
    ):
        pass
