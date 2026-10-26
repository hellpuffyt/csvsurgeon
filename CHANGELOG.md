# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Fields beyond the header's width in long rows were dropped from the output
  without any notice (the 0.1.0 notes said they were kept in an `_overflow`
  column; the CLI never wrote one). The output is unchanged, but the CLI now
  prints a warning on stderr with the number of affected rows.
- `-o` naming the input file truncated the input while it was being read
  (a 20,000-row file came back with about 6,000 rows). It is now refused.
- A header that repeats a column name silently replaced one column's values
  with the other's. It is now refused with a message naming the column.
- Text such as `nan`, `inf`, `Infinity` and `1_000` was treated as a number,
  so `$name == 'Nan'` never matched. Only plain decimal notation is numeric now.
- Deeply nested expressions, infinite arguments to `substr`/`round`/`split`,
  over-long fields, undecodable input and unknown `--encoding` values crashed
  with a Python traceback; each is now an error message with exit code 2.

### Changed

- README: install from source (the package is not on PyPI), memory figures
  re-measured with the command shown, and a section on limits and refusals.

### Added

- `SECURITY.md`, `.editorconfig`, `docs/PRODUCTION_READINESS.md`.

## [0.1.0] — 2026-08-31

First release.

### Added

- Streaming CSV transforms that never load the file into memory: `--filter`,
  `--compute`, `--select`, `--drop`, `--rename`, `--fill`, `--head`, `--skip`.
  Operations apply in the order given on the command line, so a filter written
  after a computation can test the computed column.
- A safe expression language — a real tokenizer, parser and evaluator rather
  than `eval`. Column references (`$name`, bare `name`, `$"with spaces"`),
  literals, `and`/`or`/`not` with short-circuiting, comparison, arithmetic,
  a case-insensitive `~` contains, and 16 functions.
- Numeric-aware comparison: when both sides of a comparison look like numbers
  they are compared as numbers, so `$age > 30` is not defeated by every CSV
  field being text.
- Column names validated against the header before the first row is read, so a
  typo fails immediately on a file of any size.
- Dialect sniffing (`,` `;` tab `|`) with the sample pushed back, so the header
  is never consumed by the sniff.
- Ragged-row handling: short rows padded, long rows keeping their extra fields
  under an `_overflow` column.
- `--count`, `--header-only`, `-d/--delimiter`, `--output-delimiter`,
  `--encoding`, and stdin/stdout support.
- Errors that point at the offending character with a caret, and column errors
  that list what the input actually has.

[0.1.0]: https://github.com/hellpuffyt/csvsurgeon/releases/tag/v0.1.0
