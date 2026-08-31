"""A small, safe expression language for row predicates and computed columns.

The whole point of this module is what it *cannot* do. A CSV tool that
accepts expressions has an obvious shortcut — hand the string to
``eval`` — and that shortcut hands every ``--filter`` flag the full
power of the interpreter, on data files that routinely come from
somewhere else. There is no sandbox around ``eval`` worth trusting, so
this is a real tokenizer, a real parser, and an evaluator over a fixed
set of operations. Nothing here reaches attributes, imports, indexes
arbitrary objects, or calls anything but the functions in
:data:`FUNCTIONS`.

The language:

* **Column references** — ``$name`` always, or a bare ``name`` when it
  is not a keyword. ``$`` with a quoted string (``$"total sales"``)
  reaches columns whose names have spaces.
* **Literals** — numbers (``42``, ``3.5``), strings (``'a'``, ``"a"``),
  ``true``, ``false``, ``null``.
* **Operators** — ``or``, ``and``, ``not``, ``== != < <= > >=``,
  ``+ - * / %``, unary ``-``, and ``~`` for "matches, case-insensitively
  contains". Parentheses group.
* **Functions** — see :data:`FUNCTIONS`.

Values are ``str``, ``float``, ``bool``, or ``None``. CSV gives every
field as text, so comparisons coerce: comparing a column against a
number parses the column as a number, and comparing against a string
compares text. This is the behaviour a data tool needs — ``$age > 30``
must work on a CSV — and it is applied in exactly one place
(:func:`_compare`) rather than scattered through the evaluator.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .errors import ColumnError, ExpressionError

Value = str | float | bool | None
"""Everything an expression can produce."""

Row = Mapping[str, str]
"""One CSV record, as the reader produced it: every field is text."""


# --- tokens ---------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """One lexical unit and where it started."""

    kind: str
    text: str
    position: int
    value: Value = None


KEYWORDS = {"and", "or", "not", "true", "false", "null"}

# Longest first, so `<=` is never read as `<` then `=`.
OPERATORS = (
    "==",
    "!=",
    "<=",
    ">=",
    "<",
    ">",
    "+",
    "-",
    "*",
    "/",
    "%",
    "~",
    "(",
    ")",
    ",",
)


def tokenize(source: str) -> list[Token]:
    """Splits `source` into tokens.

    Raises:
        ExpressionError: on an unterminated string or an unknown character.
    """
    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        char = source[i]

        if char.isspace():
            i += 1
            continue

        if char in "'\"":
            text, end = _read_string(source, i)
            tokens.append(Token("string", source[i:end], i, text))
            i = end
            continue

        if char == "$":
            name, end = _read_column_reference(source, i)
            tokens.append(Token("column", source[i:end], i, name))
            i = end
            continue

        if char.isdigit() or (char == "." and i + 1 < n and source[i + 1].isdigit()):
            number, end = _read_number(source, i)
            tokens.append(Token("number", source[i:end], i, number))
            i = end
            continue

        if char.isalpha() or char == "_":
            end = i
            while end < n and (source[end].isalnum() or source[end] == "_"):
                end += 1
            word = source[i:end]
            kind = "keyword" if word.lower() in KEYWORDS else "name"
            tokens.append(Token(kind, word, i))
            i = end
            continue

        for op in OPERATORS:
            if source.startswith(op, i):
                tokens.append(Token("op", op, i))
                i += len(op)
                break
        else:
            raise ExpressionError(f"unexpected character {char!r}", source, i)

    tokens.append(Token("end", "", n))
    return tokens


def _read_string(source: str, start: int) -> tuple[str, int]:
    """Reads a quoted string starting at `start`. Returns (text, end)."""
    quote = source[start]
    out: list[str] = []
    i = start + 1
    while i < len(source):
        char = source[i]
        if char == "\\" and i + 1 < len(source):
            nxt = source[i + 1]
            # A deliberately tiny escape table: enough for real data,
            # small enough to hold in your head.
            out.append({"n": "\n", "t": "\t", "\\": "\\", quote: quote}.get(nxt, nxt))
            i += 2
            continue
        if char == quote:
            return "".join(out), i + 1
        out.append(char)
        i += 1
    raise ExpressionError("unterminated string", source, start)


def _read_column_reference(source: str, start: int) -> tuple[str, int]:
    """Reads ``$name`` or ``$"quoted name"``. Returns (column, end)."""
    i = start + 1
    if i < len(source) and source[i] in "'\"":
        name, end = _read_string(source, i)
        if not name:
            raise ExpressionError("empty column name", source, start)
        return name, end
    end = i
    while end < len(source) and (source[end].isalnum() or source[end] in "_."):
        end += 1
    if end == i:
        raise ExpressionError("expected a column name after '$'", source, start)
    return source[i:end], end


def _read_number(source: str, start: int) -> tuple[float, int]:
    """Reads a decimal number. Returns (value, end)."""
    end = start
    seen_dot = False
    while end < len(source):
        char = source[end]
        if char.isdigit():
            end += 1
        elif char == "." and not seen_dot:
            seen_dot = True
            end += 1
        else:
            break
    try:
        return float(source[start:end]), end
    except ValueError:  # pragma: no cover - the scan above guarantees a number
        raise ExpressionError("malformed number", source, start) from None


# --- syntax tree ----------------------------------------------------------


class Node:
    """Base class for expression nodes."""

    def evaluate(self, row: Row) -> Value:
        raise NotImplementedError  # pragma: no cover

    def columns(self) -> set[str]:
        """Every column this node reads, for up-front validation."""
        return set()


@dataclass(frozen=True)
class Literal(Node):
    value: Value

    def evaluate(self, row: Row) -> Value:
        return self.value


@dataclass(frozen=True)
class Column(Node):
    name: str
    position: int

    def evaluate(self, row: Row) -> Value:
        try:
            return row[self.name]
        except KeyError:
            raise ColumnError(self.name, list(row)) from None

    def columns(self) -> set[str]:
        return {self.name}


@dataclass(frozen=True)
class Unary(Node):
    op: str
    operand: Node
    position: int
    source: str

    def evaluate(self, row: Row) -> Value:
        value = self.operand.evaluate(row)
        if self.op == "not":
            return not truthy(value)
        number = _as_number(value, self.source, self.position)
        return -number

    def columns(self) -> set[str]:
        return self.operand.columns()


@dataclass(frozen=True)
class Binary(Node):
    op: str
    left: Node
    right: Node
    position: int
    source: str

    def evaluate(self, row: Row) -> Value:
        # `and`/`or` short-circuit, so a guard like
        # `$x != '' and $x > 5` never evaluates the comparison on an
        # empty field.
        if self.op == "and":
            return truthy(self.left.evaluate(row)) and truthy(self.right.evaluate(row))
        if self.op == "or":
            return truthy(self.left.evaluate(row)) or truthy(self.right.evaluate(row))

        left = self.left.evaluate(row)
        right = self.right.evaluate(row)

        if self.op in {"==", "!=", "<", "<=", ">", ">="}:
            return _compare(self.op, left, right, self.source, self.position)
        if self.op == "~":
            return _as_text(right).casefold() in _as_text(left).casefold()
        return _arithmetic(self.op, left, right, self.source, self.position)

    def columns(self) -> set[str]:
        return self.left.columns() | self.right.columns()


@dataclass(frozen=True)
class Call(Node):
    name: str
    args: tuple[Node, ...]
    position: int
    source: str

    def evaluate(self, row: Row) -> Value:
        function = FUNCTIONS[self.name]
        values = [arg.evaluate(row) for arg in self.args]
        if not function.accepts(len(values)):
            raise ExpressionError(
                f"{self.name}() takes {function.arity_text()}, got {len(values)}",
                self.source,
                self.position,
            )
        return function.call(values, self.source, self.position)

    def columns(self) -> set[str]:
        return set().union(*(arg.columns() for arg in self.args)) if self.args else set()


# --- coercion -------------------------------------------------------------


def truthy(value: Value) -> bool:
    """Whether `value` counts as true.

    Empty text is false, which is what makes ``--filter '$notes'`` mean
    "rows that have notes" on a CSV, where a missing value is ``''``.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value != 0.0
    return value.strip() != ""


def _as_text(value: Value) -> str:
    """Renders `value` as text, without Python's float formatting quirks."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 3.0 came from the CSV as "3"; rendering it as "3.0" would make
        # a computed column silently differ from the input it copied.
        if math.isfinite(value) and value == int(value):
            return str(int(value))
        return repr(value)
    return value


def _to_number(value: Value) -> float | None:
    """`value` as a number, or None when it is not one."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, float):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_number(value: Value, source: str, position: int) -> float:
    """`value` as a number, or an error naming what it actually was."""
    number = _to_number(value)
    if number is None:
        raise ExpressionError(
            f"expected a number, got {_as_text(value)!r}", source, position
        )
    return number


def _compare(op: str, left: Value, right: Value, source: str, position: int) -> bool:
    """Compares two values, numerically when both look like numbers.

    This is the one place coercion happens. Both sides are tried as
    numbers first: a CSV column is always text, so ``$age > 30`` would
    otherwise compare ``"9"`` against ``30`` as strings and call 9
    larger than 30.
    """
    left_number = _to_number(left)
    right_number = _to_number(right)
    if left_number is not None and right_number is not None:
        a: float | str = left_number
        b: float | str = right_number
    else:
        a = _as_text(left)
        b = _as_text(right)

    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    # mypy cannot see that a and b are the same type here, but the
    # branch above guarantees it.
    if op == "<":
        return a < b  # type: ignore[operator]
    if op == "<=":
        return a <= b  # type: ignore[operator]
    if op == ">":
        return a > b  # type: ignore[operator]
    return a >= b  # type: ignore[operator]


def _arithmetic(op: str, left: Value, right: Value, source: str, position: int) -> Value:
    """Applies an arithmetic operator.

    ``+`` concatenates when either side is text that is not a number,
    which is how computed columns join fields together.
    """
    if op == "+":
        left_number = _to_number(left)
        right_number = _to_number(right)
        if left_number is None or right_number is None:
            return _as_text(left) + _as_text(right)
        return left_number + right_number

    a = _as_number(left, source, position)
    b = _as_number(right, source, position)
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise ExpressionError(f"division by zero in {op!r}", source, position)
    if op == "/":
        return a / b
    return math.fmod(a, b)


# --- functions ------------------------------------------------------------


@dataclass(frozen=True)
class Function:
    """One callable exposed to expressions."""

    handler: Callable[[list[Value]], Value]
    min_args: int
    max_args: int | None

    def accepts(self, count: int) -> bool:
        if count < self.min_args:
            return False
        return self.max_args is None or count <= self.max_args

    def arity_text(self) -> str:
        if self.max_args == self.min_args:
            return f"{self.min_args} argument(s)"
        if self.max_args is None:
            return f"at least {self.min_args} argument(s)"
        return f"{self.min_args} to {self.max_args} arguments"

    def call(self, args: list[Value], source: str, position: int) -> Value:
        try:
            return self.handler(args)
        except ExpressionError:
            raise
        except (ValueError, TypeError, IndexError) as err:
            raise ExpressionError(str(err), source, position) from None


def _substr(args: list[Value]) -> Value:
    text = _as_text(args[0])
    start = int(_as_number(args[1], "", 0))
    if len(args) == 2:
        return text[start:]
    return text[start : start + int(_as_number(args[2], "", 0))]


def _coalesce(args: list[Value]) -> Value:
    for arg in args:
        if truthy(arg):
            return arg
    return ""


def _number(args: list[Value]) -> Value:
    """``num(x)`` — x as a number, or the fallback when it is not one."""
    parsed = _to_number(args[0])
    if parsed is not None:
        return parsed
    return args[1] if len(args) > 1 else None


FUNCTIONS: dict[str, Function] = {
    "upper": Function(lambda a: _as_text(a[0]).upper(), 1, 1),
    "lower": Function(lambda a: _as_text(a[0]).lower(), 1, 1),
    "trim": Function(lambda a: _as_text(a[0]).strip(), 1, 1),
    "len": Function(lambda a: float(len(_as_text(a[0]))), 1, 1),
    "abs": Function(lambda a: abs(_as_number(a[0], "", 0)), 1, 1),
    "round": Function(
        lambda a: float(
            round(
                _as_number(a[0], "", 0),
                int(_as_number(a[1], "", 0)) if len(a) > 1 else 0,
            )
        ),
        1,
        2,
    ),
    "num": Function(_number, 1, 2),
    "str": Function(lambda a: _as_text(a[0]), 1, 1),
    "replace": Function(
        lambda a: _as_text(a[0]).replace(_as_text(a[1]), _as_text(a[2])), 3, 3
    ),
    "contains": Function(lambda a: _as_text(a[1]) in _as_text(a[0]), 2, 2),
    "startswith": Function(lambda a: _as_text(a[0]).startswith(_as_text(a[1])), 2, 2),
    "endswith": Function(lambda a: _as_text(a[0]).endswith(_as_text(a[1])), 2, 2),
    "substr": Function(_substr, 2, 3),
    "coalesce": Function(_coalesce, 1, None),
    "concat": Function(lambda a: "".join(_as_text(x) for x in a), 1, None),
    "split": Function(
        lambda a: _as_text(a[0]).split(_as_text(a[1]))[int(_as_number(a[2], "", 0))],
        3,
        3,
    ),
}


# --- parser ---------------------------------------------------------------

# Binding powers, loosest first. A Pratt parser needs nothing else to
# get precedence and associativity right.
BINARY_PRECEDENCE: dict[str, int] = {
    "or": 1,
    "and": 2,
    "==": 3,
    "!=": 3,
    "<": 3,
    "<=": 3,
    ">": 3,
    ">=": 3,
    "~": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
}


class Parser:
    """A Pratt parser over the token stream."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.index = 0

    def peek(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        if token.kind != "end":
            self.index += 1
        return token

    def expect(self, text: str) -> Token:
        token = self.peek()
        if token.text != text:
            raise ExpressionError(
                f"expected {text!r}, found {token.text or 'end of expression'!r}",
                self.source,
                token.position,
            )
        return self.advance()

    def parse(self) -> Node:
        """Parses the whole expression, rejecting trailing junk."""
        if self.peek().kind == "end":
            raise ExpressionError("empty expression", self.source, 0)
        node = self.parse_expression(0)
        trailing = self.peek()
        if trailing.kind != "end":
            raise ExpressionError(
                f"unexpected {trailing.text!r}", self.source, trailing.position
            )
        return node

    def parse_expression(self, minimum: int) -> Node:
        left = self.parse_prefix()
        while True:
            token = self.peek()
            key = token.text.lower() if token.kind == "keyword" else token.text
            precedence = BINARY_PRECEDENCE.get(key, 0)
            if token.kind not in {"op", "keyword"} or precedence <= minimum:
                return left
            self.advance()
            # Every operator here is left-associative, so the right side
            # binds tighter by exactly one level.
            right = self.parse_expression(precedence)
            left = Binary(key, left, right, token.position, self.source)

    def parse_prefix(self) -> Node:
        token = self.advance()

        if token.kind == "number" or token.kind == "string":
            return Literal(token.value)
        if token.kind == "column":
            assert isinstance(token.value, str)
            return Column(token.value, token.position)
        if token.kind == "keyword":
            word = token.text.lower()
            if word == "true":
                return Literal(True)
            if word == "false":
                return Literal(False)
            if word == "null":
                return Literal(None)
            if word == "not":
                return Unary("not", self.parse_expression(2), token.position, self.source)
            raise ExpressionError(
                f"{token.text!r} cannot start an expression", self.source, token.position
            )
        if token.kind == "name":
            if self.peek().text == "(":
                return self.parse_call(token)
            # A bare word is a column, so `--filter 'status == \"paid\"'`
            # reads the way people expect without `$` everywhere.
            return Column(token.text, token.position)
        if token.text == "(":
            node = self.parse_expression(0)
            self.expect(")")
            return node
        if token.text == "-":
            return Unary("-", self.parse_expression(6), token.position, self.source)

        raise ExpressionError(
            f"unexpected {token.text or 'end of expression'!r}",
            self.source,
            token.position,
        )

    def parse_call(self, name: Token) -> Node:
        if name.text not in FUNCTIONS:
            known = ", ".join(sorted(FUNCTIONS))
            raise ExpressionError(
                f"unknown function {name.text!r}; known functions: {known}",
                self.source,
                name.position,
            )
        self.expect("(")
        args: list[Node] = []
        if self.peek().text != ")":
            args.append(self.parse_expression(0))
            while self.peek().text == ",":
                self.advance()
                args.append(self.parse_expression(0))
        self.expect(")")
        return Call(name.text, tuple(args), name.position, self.source)


def parse(source: str) -> Node:
    """Parses `source` into an evaluable expression.

    Raises:
        ExpressionError: when the expression is malformed.
    """
    return Parser(source).parse()


def validate_columns(node: Node, available: Sequence[str]) -> None:
    """Rejects an expression that reads a column the input lacks.

    Checked once against the header rather than per row: a transform
    over a hundred million rows must fail on row zero, not after an
    hour.

    Raises:
        ColumnError: naming the first unknown column.
    """
    known = set(available)
    for name in sorted(node.columns()):
        if name not in known:
            raise ColumnError(name, list(available))
