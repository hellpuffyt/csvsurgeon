"""Pipeline steps: header propagation, laziness, and column validation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from csvsurgeon.errors import ColumnError
from csvsurgeon.expr import Row, parse
from csvsurgeon.pipeline import (
    Compute,
    Drop,
    Fill,
    Filter,
    Head,
    Pipeline,
    Rename,
    Select,
    Skip,
)

ROWS: list[Row] = [
    {"name": "Ada", "age": "36", "city": "London"},
    {"name": "Bob", "age": "9", "city": "Paris"},
    {"name": "Cy", "age": "41", "city": "London"},
    {"name": "", "age": "20", "city": "Rome"},
]
HEADER = ["name", "age", "city"]


def run(pipeline: Pipeline, rows: list[Row] | None = None) -> list[Row]:
    return list(pipeline.apply(rows if rows is not None else ROWS))


def test_filter_keeps_matching_rows_and_leaves_the_header_alone() -> None:
    step = Filter(parse("$age > 30"), "$age > 30")
    assert step.header(HEADER) == HEADER
    kept = run(Pipeline([step]))
    assert [r["name"] for r in kept] == ["Ada", "Cy"]


def test_compute_appends_a_column_and_can_overwrite_one_in_place() -> None:
    added = Compute("shout", parse("upper($name)"), "upper($name)")
    assert added.header(HEADER) == [*HEADER, "shout"]
    assert run(Pipeline([added]))[0]["shout"] == "ADA"

    overwritten = Compute("name", parse("upper($name)"), "upper($name)")
    assert overwritten.header(HEADER) == HEADER, "overwriting must not reorder columns"
    assert run(Pipeline([overwritten]))[0]["name"] == "ADA"


def test_a_computed_number_is_written_without_a_spurious_decimal() -> None:
    step = Compute("double", parse("$age * 2"), "$age * 2")
    assert run(Pipeline([step]))[0]["double"] == "72"


def test_select_reorders_and_narrows() -> None:
    step = Select(["city", "name"])
    assert step.header(HEADER) == ["city", "name"]
    assert run(Pipeline([step]))[0] == {"city": "London", "name": "Ada"}


def test_drop_removes_columns() -> None:
    step = Drop(["age"])
    assert step.header(HEADER) == ["name", "city"]
    assert "age" not in run(Pipeline([step]))[0]


def test_rename_keeps_position() -> None:
    step = Rename({"age": "years"})
    assert step.header(HEADER) == ["name", "years", "city"]
    assert run(Pipeline([step]))[0]["years"] == "36"


def test_fill_replaces_only_empty_values() -> None:
    step = Fill("name", "unknown")
    assert step.header(HEADER) == HEADER
    names = [r["name"] for r in run(Pipeline([step]))]
    assert names == ["Ada", "Bob", "Cy", "unknown"]


def test_head_and_skip() -> None:
    assert len(run(Pipeline([Head(2)]))) == 2
    assert [r["name"] for r in run(Pipeline([Skip(2)]))] == ["Cy", ""]
    assert run(Pipeline([Head(0)])) == []
    assert len(run(Pipeline([Head(99)]))) == len(ROWS)


def test_steps_run_in_the_order_given() -> None:
    # Filtering before the computation cannot see the computed column;
    # filtering after it must.
    after = Pipeline(
        [
            Compute("shout", parse("upper($name)"), "upper($name)"),
            Filter(parse("$shout == 'ADA'"), "$shout == 'ADA'"),
        ]
    )
    assert [r["name"] for r in run(after)] == ["Ada"]

    before = Pipeline(
        [
            Filter(parse("$name == 'Ada'"), "$name == 'Ada'"),
            Compute("shout", parse("upper($name)"), "upper($name)"),
        ]
    )
    assert run(before)[0]["shout"] == "ADA"


def test_header_validation_threads_through_the_whole_pipeline() -> None:
    pipeline = Pipeline(
        [
            Compute("shout", parse("upper($name)"), "upper($name)"),
            Select(["shout"]),
        ]
    )
    assert pipeline.header(HEADER) == ["shout"]


def test_a_step_that_reads_a_column_an_earlier_step_dropped_is_refused() -> None:
    pipeline = Pipeline([Drop(["age"]), Filter(parse("$age > 1"), "$age > 1")])
    with pytest.raises(ColumnError):
        pipeline.header(HEADER)


@pytest.mark.parametrize(
    "step",
    [
        Select(["nope"]),
        Drop(["nope"]),
        Rename({"nope": "x"}),
        Fill("nope", "x"),
    ],
)
def test_every_column_step_rejects_an_unknown_column(step: object) -> None:
    with pytest.raises(ColumnError):
        step.header(HEADER)  # type: ignore[attr-defined]


def test_an_empty_pipeline_is_the_identity() -> None:
    pipeline = Pipeline([])
    assert pipeline.header(HEADER) == HEADER
    assert run(pipeline) == ROWS


# --- the memory guarantee -------------------------------------------------


def test_the_pipeline_never_reads_ahead_of_what_it_yields() -> None:
    """The design constraint, asserted rather than assumed.

    If any step buffered the stream, the source would be exhausted
    before the first output row appeared.
    """
    consumed: list[int] = []

    def counting_rows() -> Iterator[Row]:
        for index in range(1000):
            consumed.append(index)
            yield {"name": f"n{index}", "age": str(index), "city": "X"}

    pipeline = Pipeline(
        [
            Filter(parse("$age > 1"), "$age > 1"),
            Compute("shout", parse("upper($name)"), "upper($name)"),
        ]
    )
    stream = pipeline.apply(counting_rows())
    first = next(stream)

    assert first["name"] == "n2"
    assert len(consumed) <= 3, (
        f"reading one row consumed {len(consumed)} — a step is buffering"
    )


def test_head_stops_reading_the_source_entirely() -> None:
    consumed: list[int] = []

    def counting_rows() -> Iterator[Row]:
        for index in range(1_000_000):
            consumed.append(index)
            yield {"name": f"n{index}"}

    rows = list(Pipeline([Head(3)]).apply(counting_rows()))

    assert len(rows) == 3
    assert len(consumed) == 3, "head must not drain the source"
