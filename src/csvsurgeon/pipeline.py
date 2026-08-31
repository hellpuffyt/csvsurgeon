"""The streaming transform pipeline.

Every operation here is a generator over rows. That is the whole design
constraint: csvsurgeon exists to transform CSV files that do not fit in
memory, so nothing in this module may build a list of rows, sort, or
look ahead. The only state any step keeps is a bounded counter or the
header.

Steps compose in the order the user wrote them on the command line,
which matters: ``--filter`` before ``--compute`` sees the original
columns, and after it sees the computed one. Getting that order from
the argument list rather than from a fixed pipeline shape is what makes
the tool predictable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .errors import ColumnError
from .expr import Node, Row, _as_text, truthy, validate_columns


class Step:
    """One stage of the pipeline."""

    def header(self, columns: list[str]) -> list[str]:
        """The columns this step emits, given the ones it receives.

        Called once before any row is read, so a step that references a
        missing column fails immediately rather than mid-file.
        """
        return columns

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        """Transforms the row stream. Must not materialise it."""
        raise NotImplementedError  # pragma: no cover


@dataclass
class Filter(Step):
    """Keeps rows for which `predicate` is true."""

    predicate: Node
    source: str

    def header(self, columns: list[str]) -> list[str]:
        validate_columns(self.predicate, columns)
        return columns

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            if truthy(self.predicate.evaluate(row)):
                yield row


@dataclass
class Compute(Step):
    """Adds (or overwrites) `name` with the value of `expression`."""

    name: str
    expression: Node
    source: str

    def header(self, columns: list[str]) -> list[str]:
        validate_columns(self.expression, columns)
        # Overwriting an existing column keeps its position, so a
        # computed correction does not reorder the output.
        return columns if self.name in columns else [*columns, self.name]

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            updated = dict(row)
            updated[self.name] = _as_text(self.expression.evaluate(row))
            yield updated


@dataclass
class Select(Step):
    """Keeps only `names`, in the order given."""

    names: list[str]

    def header(self, columns: list[str]) -> list[str]:
        for name in self.names:
            if name not in columns:
                raise ColumnError(name, columns)
        return list(self.names)

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            yield {name: row.get(name, "") for name in self.names}


@dataclass
class Drop(Step):
    """Removes `names`."""

    names: list[str]

    def header(self, columns: list[str]) -> list[str]:
        for name in self.names:
            if name not in columns:
                raise ColumnError(name, columns)
        return [c for c in columns if c not in set(self.names)]

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        dropped = set(self.names)
        for row in rows:
            yield {k: v for k, v in row.items() if k not in dropped}


@dataclass
class Rename(Step):
    """Renames columns, keeping their positions."""

    mapping: dict[str, str]

    def header(self, columns: list[str]) -> list[str]:
        for name in self.mapping:
            if name not in columns:
                raise ColumnError(name, columns)
        return [self.mapping.get(c, c) for c in columns]

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            yield {self.mapping.get(k, k): v for k, v in row.items()}


@dataclass
class Head(Step):
    """Stops after `count` rows.

    Closing the stream early is the point: `head` on a 200 GB file must
    read the first few kilobytes, not all of it.
    """

    count: int

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        if self.count <= 0:
            return
        for index, row in enumerate(rows):
            yield row
            if index + 1 >= self.count:
                return


@dataclass
class Skip(Step):
    """Drops the first `count` rows."""

    count: int

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for index, row in enumerate(rows):
            if index >= self.count:
                yield row


@dataclass
class Fill(Step):
    """Replaces empty values in `name` with `value`."""

    name: str
    value: str

    def header(self, columns: list[str]) -> list[str]:
        if self.name not in columns:
            raise ColumnError(self.name, columns)
        return columns

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            if row.get(self.name, "").strip():
                yield row
            else:
                updated = dict(row)
                updated[self.name] = self.value
                yield updated


@dataclass
class Pipeline:
    """An ordered list of steps, applied as one lazy stream."""

    steps: list[Step]

    def header(self, columns: list[str]) -> list[str]:
        """The output header, validating every step against the input.

        Raises:
            ColumnError: when a step references a column that is not
                present at that point in the pipeline.
        """
        current = list(columns)
        for step in self.steps:
            current = step.header(current)
        return current

    def apply(self, rows: Iterable[Row]) -> Iterator[Row]:
        """Runs every step, lazily."""
        stream: Iterator[Row] = iter(rows)
        for step in self.steps:
            stream = step.apply(stream)
        return stream
