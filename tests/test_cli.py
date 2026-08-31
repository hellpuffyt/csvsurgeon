"""The command line, driven the way a user drives it."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from csvsurgeon.cli import build_parser, build_pipeline, main, run
from csvsurgeon.errors import UsageError

CSV = "name,age,city\nAda,36,London\nBob,9,Paris\nCy,41,London\n"


def invoke(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """Runs the CLI over in-memory streams, returning (code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = run(argv, out, err, io.StringIO(stdin) if stdin is not None else None)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def csv_file(tmp_path: Path) -> str:
    path = tmp_path / "people.csv"
    path.write_text(CSV, encoding="utf-8")
    return str(path)


# --- the basics -----------------------------------------------------------


def test_with_no_operations_the_file_passes_through(csv_file: str) -> None:
    code, out, err = invoke([csv_file])
    assert code == 0, err
    assert out == CSV


def test_reading_from_stdin() -> None:
    code, out, _ = invoke(["-", "--filter", "$age > 30"], stdin=CSV)
    assert code == 0
    assert "Ada" in out and "Bob" not in out


def test_input_defaults_to_stdin_when_omitted() -> None:
    code, out, _ = invoke(["--head", "1"], stdin=CSV)
    assert code == 0
    assert out == "name,age,city\nAda,36,London\n"


def test_writing_to_a_file(csv_file: str, tmp_path: Path) -> None:
    destination = tmp_path / "out.csv"
    code, out, err = invoke([csv_file, "-o", str(destination), "--filter", "$age > 30"])
    assert code == 0, err
    assert out == ""
    written = destination.read_text(encoding="utf-8")
    assert "Ada" in written and "Bob" not in written


# --- operations -----------------------------------------------------------


def test_filter(csv_file: str) -> None:
    _, out, _ = invoke([csv_file, "--filter", "$city == 'London'"])
    assert out == "name,age,city\nAda,36,London\nCy,41,London\n"


def test_compute_adds_a_column(csv_file: str) -> None:
    _, out, _ = invoke([csv_file, "--compute", "loud=upper($name)"])
    assert out.splitlines()[0] == "name,age,city,loud"
    assert out.splitlines()[1] == "Ada,36,London,ADA"


def test_select_and_drop(csv_file: str) -> None:
    _, out, _ = invoke([csv_file, "--select", "city,name"])
    assert out.splitlines()[0] == "city,name"

    _, out, _ = invoke([csv_file, "--drop", "age"])
    assert out.splitlines()[0] == "name,city"


def test_rename(csv_file: str) -> None:
    _, out, _ = invoke([csv_file, "--rename", "age=years"])
    assert out.splitlines()[0] == "name,years,city"


def test_head_and_skip(csv_file: str) -> None:
    _, out, _ = invoke([csv_file, "--head", "1"])
    assert len(out.splitlines()) == 2

    _, out, _ = invoke([csv_file, "--skip", "2"])
    assert out.splitlines()[1] == "Cy,41,London"


def test_fill(tmp_path: Path) -> None:
    path = tmp_path / "gaps.csv"
    path.write_text("name,city\nAda,\nBob,Paris\n", encoding="utf-8")
    _, out, _ = invoke([str(path), "--fill", "city=unknown"])
    assert "Ada,unknown" in out


def test_count_writes_a_number_not_rows(csv_file: str) -> None:
    code, out, _ = invoke([csv_file, "--filter", "$age > 30", "--count"])
    assert code == 0
    assert out.strip() == "2"


def test_header_only_shows_the_resulting_header(csv_file: str) -> None:
    code, out, _ = invoke(
        [csv_file, "--compute", "x=1", "--select", "x,name", "--header-only"]
    )
    assert code == 0
    assert out.strip() == "x,name"


def test_operations_apply_in_command_line_order(csv_file: str) -> None:
    # The computed column must be visible to a filter written after it.
    _, out, _ = invoke(
        [csv_file, "--compute", "big=$age > 20", "--filter", "$big == 'true'"]
    )
    names = [line.split(",")[0] for line in out.splitlines()[1:]]
    assert names == ["Ada", "Cy"]


def test_a_chain_of_operations_composes(csv_file: str) -> None:
    _, out, _ = invoke(
        [
            csv_file,
            "--filter",
            "$age > 10",
            "--compute",
            "label=lower($city) + '-' + $name",
            "--select",
            "label",
            "--head",
            "1",
        ]
    )
    assert out == "label\nlondon-Ada\n"


def test_delimiters(tmp_path: Path) -> None:
    path = tmp_path / "semi.csv"
    path.write_text("name;age\nAda;36\n", encoding="utf-8")
    _, out, _ = invoke([str(path), "-d", ";", "--output-delimiter", "|"])
    assert out == "name|age\nAda|36\n"


# --- failures -------------------------------------------------------------


def test_an_unknown_column_fails_before_writing_anything(csv_file: str) -> None:
    code, out, err = invoke([csv_file, "--filter", "$nope == 1"])
    assert code == 2
    assert out == "", "nothing may be written when the pipeline is invalid"
    assert "nope" in err


def test_a_malformed_expression_points_at_the_problem(csv_file: str) -> None:
    code, _, err = invoke([csv_file, "--filter", "1 +"])
    assert code == 2
    assert "^" in err


def test_a_missing_file_is_reported_by_name() -> None:
    code, _, err = invoke(["definitely-not-here.csv"])
    assert code == 2
    assert "no such file" in err


def test_empty_input_is_reported() -> None:
    code, _, err = invoke(["-"], stdin="")
    assert code == 2
    assert "empty" in err


@pytest.mark.parametrize(
    ("argv", "fragment"),
    [
        (["--compute", "noequals"], "NAME=VALUE"),
        (["--fill", "noequals"], "NAME=VALUE"),
        (["--compute", "=1"], "NAME=VALUE"),
        (["--select", " , "], "at least one"),
        (["--head", "x"], "whole number"),
        (["--head", "-1"], "negative"),
    ],
)
def test_malformed_flags_are_reported_clearly(
    csv_file: str, argv: list[str], fragment: str
) -> None:
    code, _, err = invoke([csv_file, *argv])
    assert code == 2
    assert fragment in err


def test_build_pipeline_rejects_an_unknown_operation() -> None:
    with pytest.raises(UsageError):
        build_pipeline([("nonsense", "x")])


# --- entry points ---------------------------------------------------------


def test_main_returns_an_exit_code(csv_file: str) -> None:
    assert main([csv_file, "--count"]) == 0


def test_version_is_reported() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as err:
        parser.parse_args(["--version"])
    assert err.value.code == 0


def test_help_mentions_the_ordering_rule() -> None:
    assert "in the order" in (build_parser().epilog or "")
