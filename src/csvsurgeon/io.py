"""Reading and writing CSV, one row at a time.

Two details here are the difference between a tool that works on real
files and one that works on examples:

* **Dialect.** Delimiter and quoting are sniffed from a small sample
  when the user does not say, and the sample is pushed back so the
  first row is never consumed by the sniff.
* **Ragged rows.** Real exports have short and long rows.
  :func:`read_rows` fills short rows with empty strings and collects
  overflow under a single well-known key, so a malformed row is visible
  in the output rather than crashing the run or silently vanishing.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from .errors import UsageError
from .expr import Row

OVERFLOW_KEY = "_overflow"
"""Where fields beyond the header's width are collected."""

SNIFF_BYTES = 64 * 1024
"""How much of the input is examined to guess the dialect."""


def sniff_dialect(sample: str, default_delimiter: str = ",") -> type[csv.Dialect]:
    """Guesses the CSV dialect from `sample`.

    Falls back to a plain comma dialect rather than raising: a file the
    sniffer cannot read is usually a single-column file, which parses
    correctly under the default.
    """
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:

        class Fallback(csv.excel):
            delimiter = default_delimiter

        return Fallback


def read_rows(
    stream: TextIO, delimiter: str | None = None
) -> tuple[list[str], Iterator[Row]]:
    """Reads `stream` as CSV. Returns (header, lazy rows).

    The rows iterator holds the file open, so the caller must consume it
    before the stream closes.

    Raises:
        UsageError: when the input has no header row.
    """
    if delimiter is None:
        sample = stream.read(SNIFF_BYTES)
        if not sample:
            raise UsageError("input is empty: expected a CSV header row")
        # Reading the sample consumed it, so put it back in front of the
        # rest of the file instead of seeking (stdin cannot seek).
        dialect = sniff_dialect(sample)
        reader = csv.reader(_chain_lines(sample, stream), dialect)
    else:
        reader = csv.reader(stream, delimiter=delimiter)

    try:
        header = next(reader)
    except StopIteration:
        raise UsageError("input is empty: expected a CSV header row") from None

    header = [name.strip() for name in header]
    if not any(header):
        raise UsageError("the header row is blank")
    # Rows are keyed by column name, so a repeated name would make one
    # column silently overwrite the other in every row of the output.
    seen: set[str] = set()
    for name in header:
        if name in seen:
            raise UsageError(
                f"the header repeats column {name!r}; "
                "column names must be unique (rename it in the source file)"
            )
        seen.add(name)

    return header, _rows(reader, header)


def _rows(reader: Iterable[list[str]], header: list[str]) -> Iterator[Row]:
    """Pairs each record with the header, tolerating ragged rows."""
    width = len(header)
    for record in reader:
        # A trailing newline yields an empty record; skipping it keeps a
        # file that ends in a blank line from producing a phantom row.
        if not record:
            continue
        if len(record) < width:
            record = [*record, *[""] * (width - len(record))]
        row = dict(zip(header, record[:width], strict=False))
        if len(record) > width:
            row[OVERFLOW_KEY] = ",".join(record[width:])
        yield row


def _chain_lines(sample: str, rest: TextIO) -> Iterator[str]:
    """Yields `sample`'s lines, then the rest of `rest`'s.

    `csv.reader` takes any iterable of lines, so pushing the sniffed
    sample back needs a generator rather than a fake file object. The
    sample almost always ends mid-line, so its tail is joined to the
    stream's next line — splitting a record there would corrupt it.
    """
    lines = sample.splitlines(keepends=True)
    tail = ""
    if lines and not lines[-1].endswith(("\n", "\r")):
        tail = lines.pop()
    yield from lines
    first = rest.readline()
    if tail or first:
        yield tail + first
    if first:
        yield from rest


def write_rows(
    stream: TextIO,
    header: list[str],
    rows: Iterable[Row],
    delimiter: str = ",",
) -> int:
    """Writes `rows` under `header`. Returns how many were written.

    Values not in the header are dropped, and missing ones are written
    empty, so the output is always rectangular even when the input was
    not.
    """
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    count = 0
    for row in rows:
        writer.writerow([row.get(name, "") for name in header])
        count += 1
    return count


@contextmanager
def open_input(
    path: str | None, encoding: str, default: TextIO | None = None
) -> Iterator[TextIO]:
    """Opens `path`, or `default` (stdin) when it is None or ``-``.

    `default` exists so a caller can supply the streams it wants used —
    which is what makes the command line testable without reassigning
    `sys.stdin` around every call.
    """
    if path is None or path == "-":
        yield default if default is not None else sys.stdin
        return
    source = Path(path)
    if not source.exists():
        raise UsageError(f"no such file: {path}")
    # newline="" is required: csv handles line endings itself, and
    # letting Python translate them corrupts quoted fields with
    # embedded newlines.
    with source.open("r", encoding=encoding, newline="") as handle:
        yield handle


@contextmanager
def open_output(
    path: str | None, encoding: str, default: TextIO | None = None
) -> Iterator[TextIO]:
    """Opens `path` for writing, or `default` (stdout) when None or ``-``."""
    if path is None or path == "-":
        yield default if default is not None else sys.stdout
        return
    destination = Path(path)
    if destination.parent and not destination.parent.exists():
        raise UsageError(f"no such directory: {destination.parent}")
    with destination.open("w", encoding=encoding, newline="") as handle:
        yield handle
