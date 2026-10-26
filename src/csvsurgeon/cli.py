"""The ``csvsurgeon`` command line.

Operations are collected **in the order they appear on the command
line**, not grouped by kind. ``--filter A --compute x=E --filter B``
runs A, then the computation, then B, so B can test the computed
column. Argparse does not do this on its own, so the ordering is
recovered by giving every operation flag the same destination list
(:class:`_Collect`).
"""

from __future__ import annotations

import argparse
import codecs
import csv
import os
import sys
from collections.abc import Iterator, Sequence
from typing import TextIO

from . import __version__
from .errors import CsvSurgeonError, UsageError
from .expr import Row, parse
from .io import OVERFLOW_KEY, open_input, open_output, read_rows, write_rows
from .pipeline import (
    Compute,
    Drop,
    Fill,
    Filter,
    Head,
    Pipeline,
    Rename,
    Select,
    Skip,
    Step,
)

PROGRAM = "csvsurgeon"


class _Collect(argparse.Action):
    """Appends ``(flag, value)`` to one shared list, preserving order."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        operations = getattr(namespace, "operations", None)
        if operations is None:
            operations = []
            namespace.operations = operations
        operations.append((self.dest, values))


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, kept separate so tests can inspect it."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Stream-transform very large CSV files with a small, safe "
            "expression language. The file is never loaded into memory."
        ),
        epilog=(
            "Operations run in the order given, so --filter after "
            "--compute can test the computed column."
        ),
    )
    parser.add_argument("input", nargs="?", help="input CSV, or - for stdin")
    parser.add_argument("-o", "--output", help="output CSV, or - for stdout")
    parser.add_argument(
        "-d",
        "--delimiter",
        help="input delimiter; sniffed from the file when omitted",
    )
    parser.add_argument(
        "--output-delimiter", default=",", help="output delimiter (default: ,)"
    )
    parser.add_argument("--encoding", default="utf-8", help="text encoding")
    parser.add_argument(
        "--count",
        action="store_true",
        help="write only the number of rows that survive, not the rows",
    )
    parser.add_argument(
        "--header-only",
        action="store_true",
        help="write the resulting header and stop, reading no rows",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")

    operations = parser.add_argument_group("operations (applied in order)")
    operations.add_argument(
        "--filter", action=_Collect, metavar="EXPR", help="keep rows where EXPR is true"
    )
    operations.add_argument(
        "--compute",
        action=_Collect,
        metavar="NAME=EXPR",
        help="add or overwrite column NAME with EXPR",
    )
    operations.add_argument(
        "--select", action=_Collect, metavar="COLS", help="keep only these columns"
    )
    operations.add_argument(
        "--drop", action=_Collect, metavar="COLS", help="remove these columns"
    )
    operations.add_argument(
        "--rename", action=_Collect, metavar="OLD=NEW,...", help="rename columns"
    )
    operations.add_argument(
        "--fill", action=_Collect, metavar="NAME=VALUE", help="fill empty NAME with VALUE"
    )
    operations.add_argument(
        "--head", action=_Collect, metavar="N", help="stop after N rows"
    )
    operations.add_argument(
        "--skip", action=_Collect, metavar="N", help="drop the first N rows"
    )
    return parser


def _split_pair(raw: str, flag: str) -> tuple[str, str]:
    """Splits ``NAME=VALUE``, requiring a non-empty name."""
    name, separator, value = raw.partition("=")
    if not separator or not name.strip():
        raise UsageError(f"--{flag} expects NAME=VALUE, got {raw!r}")
    return name.strip(), value


def _split_list(raw: str, flag: str) -> list[str]:
    """Splits a comma-separated column list, rejecting an empty one."""
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        raise UsageError(f"--{flag} expects at least one column name")
    return names


def _positive_int(raw: str, flag: str) -> int:
    """Parses a non-negative row count."""
    try:
        value = int(raw)
    except ValueError:
        raise UsageError(f"--{flag} expects a whole number, got {raw!r}") from None
    if value < 0:
        raise UsageError(f"--{flag} cannot be negative")
    return value


def build_pipeline(operations: Sequence[tuple[str, str]]) -> Pipeline:
    """Turns collected ``(flag, value)`` pairs into a pipeline.

    Raises:
        UsageError: on a malformed flag value.
        ExpressionError: on a malformed expression.
    """
    steps: list[Step] = []
    for flag, raw in operations:
        if flag == "filter":
            steps.append(Filter(parse(raw), raw))
        elif flag == "compute":
            name, expression = _split_pair(raw, "compute")
            steps.append(Compute(name, parse(expression), expression))
        elif flag == "select":
            steps.append(Select(_split_list(raw, "select")))
        elif flag == "drop":
            steps.append(Drop(_split_list(raw, "drop")))
        elif flag == "rename":
            mapping = dict(
                _split_pair(part, "rename") for part in _split_list(raw, "rename")
            )
            steps.append(Rename(mapping))
        elif flag == "fill":
            name, value = _split_pair(raw, "fill")
            steps.append(Fill(name, value))
        elif flag == "head":
            steps.append(Head(_positive_int(raw, "head")))
        elif flag == "skip":
            steps.append(Skip(_positive_int(raw, "skip")))
        else:  # pragma: no cover - argparse restricts the flag set
            raise UsageError(f"unknown operation {flag!r}")
    return Pipeline(steps)


def _check_encoding(encoding: str) -> None:
    """Rejects an unknown --encoding before any file is opened."""
    try:
        codecs.lookup(encoding)
    except LookupError:
        raise UsageError(f"unknown encoding {encoding!r}") from None


def _refuse_overwriting_input(source: str | None, output: str | None) -> None:
    """Refuses ``-o`` pointing at the input file.

    Opening the output truncates it while the input is still being read,
    so everything past the first buffered chunk would be lost.
    """
    if source in (None, "-") or output in (None, "-"):
        return
    assert source is not None and output is not None
    if (
        os.path.exists(source)
        and os.path.exists(output)
        and os.path.samefile(source, output)
    ):
        raise UsageError(
            f"refusing to overwrite the input file {source!r}; "
            "write to a different file and rename it afterwards"
        )


class _OverflowCounter:
    """Counts rows that arrive with fields beyond the header's width.

    :func:`write_rows` writes only the header's columns, so those extra
    fields never reach the output. Counting them lets the command say so
    on stderr instead of dropping data silently.
    """

    def __init__(self) -> None:
        self.rows = 0

    def watch(self, rows: Iterator[Row]) -> Iterator[Row]:
        for row in rows:
            if OVERFLOW_KEY in row:
                self.rows += 1
            yield row

    def report(self, stderr: TextIO) -> None:
        if self.rows:
            noun = "row" if self.rows == 1 else "rows"
            print(
                f"{PROGRAM}: warning: {self.rows} {noun} had more fields than "
                "the header; the extra fields were dropped from the output",
                file=stderr,
            )


def run(
    argv: Sequence[str] | None,
    stdout: TextIO,
    stderr: TextIO,
    stdin: TextIO | None = None,
) -> int:
    """Runs the tool against the given streams. Returns the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    operations: list[tuple[str, str]] = getattr(args, "operations", []) or []

    try:
        _check_encoding(args.encoding)
        _refuse_overwriting_input(args.input, args.output)
        pipeline = build_pipeline(operations)
        with open_input(args.input, args.encoding, stdin) as source:
            header, rows = read_rows(source, args.delimiter)
            # Validated against the header before a single row is read,
            # so a typo fails in milliseconds on a file of any size.
            out_header = pipeline.header(header)

            if args.header_only:
                print(args.output_delimiter.join(out_header), file=stdout)
                return 0

            overflow = _OverflowCounter()
            transformed = pipeline.apply(overflow.watch(rows))

            if args.count:
                count = sum(1 for _ in transformed)
                print(count, file=stdout)
                return 0

            with open_output(args.output, args.encoding, stdout) as destination:
                write_rows(destination, out_header, transformed, args.output_delimiter)
            overflow.report(stderr)
        return 0
    except CsvSurgeonError as err:
        print(f"{PROGRAM}: {err}", file=stderr)
        return 2
    except csv.Error as err:
        print(f"{PROGRAM}: malformed CSV input: {err}", file=stderr)
        return 2
    except UnicodeDecodeError as err:
        print(
            f"{PROGRAM}: input is not valid {args.encoding} ({err.reason} at byte "
            f"{err.start}); pass --encoding",
            file=stderr,
        )
        return 2
    except BrokenPipeError:  # pragma: no cover - depends on the consumer
        # `csvsurgeon big.csv | head` closes the pipe under us. That is
        # normal use, not an error worth a traceback.
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    return run(argv, sys.stdout, sys.stderr, sys.stdin)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
