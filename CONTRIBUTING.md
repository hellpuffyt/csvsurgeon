# Contributing

Thanks for taking a look.

## Getting set up

```bash
git clone https://github.com/hellpuffyt/csvsurgeon
cd csvsurgeon
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before opening a pull request

All four must pass; CI runs the same commands.

```bash
pytest -q
ruff check .
mypy
pytest -q --cov=csvsurgeon --cov-report=term-missing
```

## Design constraints

These are the three rules the whole project is built around. A change that
breaks one of them is not a trade-off to discuss — it is a different tool.

- **Nothing may buffer the row stream.** Every step is a generator that reads
  one row and yields one row. No sorting, no lookahead, no accumulating list.
  This is what lets csvsurgeon transform a file larger than RAM, and it is
  enforced by tests in `tests/test_pipeline.py` that count how much of the
  source was consumed. If you need an operation that is inherently buffering
  (a sort, a join, a group-by), it does not belong here.
- **The expression language never reaches Python.** No `eval`, no `exec`, no
  attribute access, no imports, no calling anything outside `FUNCTIONS`. Filter
  expressions arrive with data files, and data files come from elsewhere.
- **No runtime dependencies.** The tool has to be installable anywhere without
  argument. Development dependencies are fine.

## Adding a function to the expression language

1. Add it to `FUNCTIONS` in `src/csvsurgeon/expr.py` with its real arity. The
   parser's allow-list is that dictionary, so nothing else is needed to make it
   callable — and nothing not in it can ever be called.
2. Take arguments as `Value` and go through `_as_text` / `_as_number` rather
   than assuming a type. Every CSV field arrives as text.
3. Add tests for the ordinary case **and** for what it does with an empty
   field, since that is what real exports are full of.
4. Add it to the function list in `README.md` and a changelog entry.

## Adding a pipeline step

1. Subclass `Step` in `src/csvsurgeon/pipeline.py`.
2. Implement `header()` so the step validates its column references against the
   header and reports the columns it emits. This is what makes a typo fail
   before the first row is read instead of an hour into a large file.
3. Implement `apply()` as a generator. See the first design constraint.
4. Wire the flag into `build_pipeline` in `cli.py`.

## Reporting a bug

A small CSV that reproduces it is worth more than a description — ten rows is
usually plenty. Include the exact command line, since operation order changes
the result by design. If the data is not yours to share, a file with the same
shape (same delimiter, same quoting, same ragged rows) reproduces almost every
real bug in this tool.
