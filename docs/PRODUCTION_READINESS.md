# Production readiness — csvsurgeon

Audit date: 2026-10-22. Tested commit: `b48ea36` (branch `audit/production-readiness`, based on `main` @ `cc81bb0`).

## Scope

A command-line tool and library that stream-transforms CSV files (filter, compute, select, drop,
rename, fill, head, skip) using a purpose-built expression language. Architecture: `io.py`
(dialect sniffing, lazy row reader, writer) → `pipeline.py` (one generator per step) → `cli.py`
(argument order preserved). `expr.py` is a tokenizer + Pratt parser + tree evaluator over a fixed
function table; there is no `eval`, `exec`, `getattr` or import anywhere in the evaluator.

## Platforms and prerequisites

CPython 3.10–3.13, no runtime dependencies. Verified here on macOS 27 (Apple M5, arm64) with
CPython 3.12.14. Linux/Windows are covered by the existing CI matrix, not re-run in this audit.

## Clean-clone verification

```bash
git clone -b audit/production-readiness <local clone> csvsurgeon && cd csvsurgeon
uv venv --python 3.12 .venv && . .venv/bin/activate   # README uses python -m venv; equivalent
uv pip install .                                       # exit 0
csvsurgeon --version                                   # csvsurgeon 0.1.0
csvsurgeon examples/people.csv --filter '$age > 30'    # Ada, Cy rows
uv pip install -e ".[dev]" && pytest -q && ruff check . && mypy   # all exit 0
```

## Commands and results

| Command | Baseline (`cc81bb0`) | After (`b48ea36`) |
| --- | --- | --- |
| `pytest -q` | exit 0 — 188 passed, 0 failed, 0 skipped | exit 0 — 206 passed, 0 failed, 0 skipped |
| `ruff check .` | exit 0 | exit 0 |
| `mypy` (strict) | exit 0 | exit 0 |
| `pytest --cov=csvsurgeon` | — | 99% line coverage |
| CI dogfood example (from `ci.yml`) | — | output matches expected |
| `uvx pip-audit` (installed env) | — | exit 0, no known vulnerabilities |

CI status — default branch: success (`cc81bb0`, 2026-08-31); audit branch: pending until pushed.

## Security review

- **Expression language cannot execute code.** Verified by reading the evaluator (no dynamic
  dispatch other than a dict lookup in `FUNCTIONS`, unknown names rejected at parse time) and by
  tests: `__import__('os')`, `().__class__`, `open('x')`, `exec`, `eval`, `getattr(...)`,
  `upper.__call__(...)` all fail to parse; `__class__`, `$__globals__`, `$"__builtins__"`,
  `$a.__dict__` can only ever be CSV column names (`test_no_expression_can_reach_python`,
  `test_dunder_and_attribute_spellings_are_only_ever_column_names`).
- **Resource exhaustion.** Rows are generators end to end; per-row memory is bounded by the
  stdlib `csv` field limit (128 KiB per field, now a clean error). Expression nesting is bounded
  at 100 levels (previously a `RecursionError`). String repetition does not exist (`*` is numeric only).
- Secret scan of the full history (1 commit before this audit): no hits. No sensitive filenames.
- `pip-audit`: no known vulnerabilities (no runtime dependencies).
- Workflow permissions: `contents: read`. Actions are tag-pinned (`@v4`/`@v5`), not SHA-pinned — left
  as is per audit rules (not broken).

## Bugs found and fixed (each with a regression test)

| Severity | Bug | Test |
| --- | --- | --- |
| High (data loss) | `-o` naming the input file truncated it mid-read: 20,000 rows → ~6,200 | `test_output_over_the_input_file_is_refused_and_the_input_survives` |
| High (silent corruption) | Header `a,a` → first column's values replaced by the second's | `test_a_repeated_header_name_is_refused_rather_than_merged` |
| Medium (wrong result) | `nan`/`inf`/`Infinity`/`1_000` parsed as floats; `$name == 'Nan'` never matched | `test_words_python_reads_as_floats_are_still_text`, `test_underscored_digits_are_text_not_numbers` |
| Low (crash) | `RecursionError` on deep nesting; `OverflowError` on `substr(x, inf)`; `csv.Error`, `UnicodeDecodeError`, `LookupError` tracebacks | `test_pathologically_deep_expressions_are_refused`, `test_an_infinite_argument_is_an_expression_error_not_a_crash`, `test_a_field_over_the_csv_limit_is_a_clean_error`, `test_undecodable_input_is_a_clean_error`, `test_an_unknown_encoding_is_a_usage_error` |
| Docs | README said `pip install csvsurgeon`; the package is not on PyPI (404) | README now installs from source |
| Docs | README memory table (4.5 MB) not reproducible here; re-measured with the command shown | README |

Behaviour change: `1_000` and `nan`-like text are no longer numeric. Justification: Python's
`float()` accepts these, but no CSV numeric convention does, and NaN broke equality.

## Performance methodology

`/usr/bin/time -l csvsurgeon sales.csv --filter "$amount > 100 and $region ~ 'north'" --count` on a
generated 3,000,000-row, 63 MB file vs a 3-row file: peak RSS 21.9 MB for both; 6.9 s vs 0.06 s.
The count (600,454) was cross-checked with `awk`. `--head 3` on the large file: 0.07 s.

## Known limitations

- Fields over 128 KiB are rejected (stdlib `csv` limit), not raised automatically.
- Overflow fields of ragged rows are re-joined with `,` regardless of the input delimiter.
- No CSV-formula-injection escaping on output (not an advertised feature).
- `ruff format --check` reports 2 pre-existing files (README snippet, `tests/test_io.py`); CI does not run it.

## Acceptance checklist

| # | Item | Expected | Command / test | Result |
| --- | --- | --- | --- | --- |
| 1 | No code execution via expressions | attacks fail to parse or are column names | `test_no_expression_can_reach_python`, `test_dunder_...` | PASS |
| 2 | Bounded memory on large input | same RSS for 3 rows and 3M rows | `/usr/bin/time -l` run above | PASS (21.9 MB / 21.9 MB) |
| 3 | `--head` does not read the rest | stops early | `test_head_stops_reading_the_source_entirely`; 0.07 s on 63 MB | PASS |
| 4 | Numeric comparison of text fields | `9 > 30` false | `tests/test_expr.py` | PASS |
| 5 | Operations apply in CLI order | filter after compute sees column | CI dogfood example | PASS |
| 6 | Unknown column fails before reading rows | exit 2 with column list | README example run | PASS |
| 7 | Never overwrites/loses input | `-o input` refused, input unchanged | `test_output_over_the_input_file_...` | PASS |
| 8 | Bad input → message, not traceback | exit 2 | 4 CLI regression tests | PASS |
| 9 | Clean clone installs and passes | install + tests + quick start | section above | PASS |

## Classification proposal

**Production-Ready for Its Documented Scope** is proposed for the lead's independent verification;
Linux/Windows were not re-run locally (CI matrix covers them once the branch is pushed).
