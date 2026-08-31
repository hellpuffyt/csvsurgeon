"""Errors raised by csvsurgeon.

Every failure a user can cause is one of these, carries the offending
text, and knows where in the expression it happened. A tool that
rewrites data files is only as trustworthy as its error messages: a
transform that fails halfway with a stack trace is worse than one that
refuses to start.
"""

from __future__ import annotations


class CsvSurgeonError(Exception):
    """Base class for every error this package raises deliberately."""


class ExpressionError(CsvSurgeonError):
    """An expression could not be parsed or evaluated.

    ``position`` is the offset into the source expression, so the CLI can
    point at the exact character rather than saying "syntax error".
    """

    def __init__(self, message: str, source: str, position: int) -> None:
        self.source = source
        self.position = position
        super().__init__(message)

    def caret(self) -> str:
        """The expression with a caret under the offending character."""
        clamped = max(0, min(self.position, len(self.source)))
        return f"{self.source}\n{' ' * clamped}^"

    def __str__(self) -> str:
        return f"{super().__str__()}\n{self.caret()}"


class ColumnError(CsvSurgeonError):
    """A column was referenced that the input does not have."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        known = ", ".join(available) if available else "(none)"
        super().__init__(f"no such column {name!r}; input has: {known}")


class UsageError(CsvSurgeonError):
    """The command line asked for something that cannot be done."""
