"""The text primitives every tool reply is built from: tables, paging envelopes, numbers."""

from __future__ import annotations

from satisfactory_mcp.presenters.text import primitives as render


def test_truncation_counts_data_rows_only():
    """Reporting 'showing 7' for 5 data rows tells the model something false."""
    out = render.table(("a", "b"), [(1, 2), (3, 4)], total=10, offset=0)
    assert "showing 2 from offset 0" in out
    assert "8 more" in out
    assert "offset=2" in out


def test_no_envelope_when_nothing_truncated():
    out = render.table(("a",), [(1,)], total=1)
    assert "match(es)" not in out


def test_only_a_paging_caller_is_told_to_page():
    """Passing ``offset`` is the caller's declaration that it HAS an offset parameter.
    Without it the envelope offered a next page from fifteen tools whose schemas reject
    the argument, which is an instruction that fails every time it is followed."""
    pages = render.table(("a",), [(1,), (2,)], total=10, offset=0)
    assert "call again with offset=2" in pages

    cannot = render.table(("a",), [(1,), (2,)], total=10)
    assert "offset=" not in cannot
    assert render.NARROW_HINT in cannot

    told = render.table(("a",), [(1,), (2,)], total=10, hint="narrow with sources=")
    assert "8 more: narrow with sources=" in told


def test_the_row_cap_argument_is_not_decorative():
    """``limit`` was accepted and ignored, so a caller that passed it and forgot to slice
    printed every row under a header claiming a cap."""
    out = render.table(("a",), [(i,) for i in range(10)], total=10, limit=3)
    assert out.splitlines()[:4] == ["a", "0", "1", "2"]
    assert "showing 3 from offset 0" in out


def test_limit_is_clamped():
    assert render.clamp(1000) == render.MAX_ROWS
    assert render.clamp(0) == 1
    assert render.clamp(None, default=10) == 10


def test_num_is_compact():
    assert render.num(5.0) == "5"
    assert render.num(2.5) == "2.5"
    assert render.num(1200.0) == "1200"
    assert render.num(None) == "-"


def test_envelope_puts_warnings_before_rows():
    out = render.envelope("SUMMARY", "rows", ["careful"])
    assert out.index("careful") < out.index("rows")
