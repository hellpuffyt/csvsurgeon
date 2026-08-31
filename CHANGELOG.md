# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
