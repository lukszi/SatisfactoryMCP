"""What ``trace_upstream`` accepts as a seed, and what it admits about the walk.

Its own parameter help lists a factory label FIRST, and both the label path and the
selector path raised ``TypeError`` -- ``resolve_factory`` hands back machine ids and they
were indexed as records. The live-save tests in ``test_trace.py`` never caught it because
they only ever pass a building name or one instance, so the seeds here are hermetic and
run in the default suite.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp import app
from satisfactory_mcp.interfaces.mcp.tools import factories as ftools
from tests.support.projections import CONSTRUCTOR, SMELTER, smelter_belted_to_constructor


@pytest.fixture
def traced(game, monkeypatch, use_world) -> WorldState:
    st = WorldState(projection=smelter_belted_to_constructor(), game=game)
    use_world(st)
    monkeypatch.setattr(app, "game", lambda: game)
    return st


def test_a_selector_is_a_seed(traced):
    """The path that crashed for every selector."""
    out = ftools.trace_upstream("building:Constructor")
    assert not out.startswith("! "), out
    assert "Smelter" in out


def test_a_factory_label_is_a_seed(traced):
    """The path the parameter help lists first, and the one that crashed hardest."""
    traced.labels.put("rod line", [CONSTRUCTOR])
    out = ftools.trace_upstream("rod line")
    assert "factory 'rod line' (1 machines)" in out
    assert "Smelter" in out


def test_a_self_contained_selection_says_so_instead_of_printing_an_empty_table(traced):
    """A whole label is the usual way to reach this: a factory that owns its own chain has
    nothing outside it upstream, and the walk crosses hundreds of belts to find that out.

    A bare header with no rows under it cannot be told from a broken tool, which is the
    reading the live 108-machine steel factory got."""
    traced.labels.put("the whole line", [SMELTER, CONSTRUCTOR])
    out = ftools.trace_upstream("the whole line")
    assert "nothing outside this selection feeds it" in out
    assert "belt/pipe node(s)" in out
    assert "building\tkind\tcount" not in out


def test_an_instance_and_a_building_name_still_seed_it(traced):
    assert "Smelter" in ftools.trace_upstream(CONSTRUCTOR)
    assert "Smelter" in ftools.trace_upstream("Constructor")


def test_the_example_ids_it_prints_resolve_as_selectors(traced):
    """Cut to their last ten characters they named nothing, anywhere in the MCP."""
    assert SMELTER in ftools.trace_upstream(CONSTRUCTOR)
    assert not ftools.factory_query(f"machine:{SMELTER}").startswith("! ")


def test_a_walk_that_may_over_report_says_how_much(traced, game, use_world):
    """``Trace.ambiguous`` was carried and never printed, so a walk over undirected
    segments read exactly like one where every edge stated its direction."""
    assert "Every edge here states its direction" in ftools.trace_upstream(CONSTRUCTOR)
    st = WorldState(projection=smelter_belted_to_constructor(belts=3), game=game)
    use_world(st)
    assert "2 edge(s) have neither" in ftools.trace_upstream(CONSTRUCTOR)


def test_a_walk_cut_short_says_it_is_a_floor(traced, monkeypatch):
    """``Trace.truncated`` too: a depth-limited walk reported as complete tells the reader
    a feeder does not exist when it was merely out of reach."""
    from satisfactory_mcp.domain.factories import trace as trace_mod

    monkeypatch.setattr(trace_mod, "MAX_HOPS", 1)
    out = ftools.trace_upstream(CONSTRUCTOR)
    assert "FLOOR" in out
    assert "Smelter" not in out, "the fixture must actually be cut short for this to mean it"


def test_resolve_seeds_hands_back_machine_leaves_for_a_label(traced, game):
    """The shared resolver the tool and ``/api/trace`` both call; a label once raised
    ``TypeError`` here because its ids were indexed as records."""
    from satisfactory_mcp.domain.factories.trace import resolve_seeds

    traced.labels.put("rod line", [CONSTRUCTOR])
    seeds, subject = resolve_seeds(traced, game, "rod line")
    assert seeds == [CONSTRUCTOR]
    assert subject == "factory 'rod line' (1 machines)"
    assert resolve_seeds(traced, game, "building:Constructor")[0] == [CONSTRUCTOR]
    assert resolve_seeds(traced, game, SMELTER)[0] == [SMELTER]
    assert resolve_seeds(traced, game, "machine:" + SMELTER) == resolve_seeds(traced, game, SMELTER)


def test_a_label_prefix_traces_the_label_even_when_it_shares_a_building_name(traced, game):
    """The dashboard and side panel seed a factory by its name, and a bare name that is
    also a building's display name matched every machine of that building instead."""
    from satisfactory_mcp.domain.factories.select import SelectorError
    from satisfactory_mcp.domain.factories.trace import resolve_seeds

    traced.labels.put("Constructor", [SMELTER])
    assert resolve_seeds(traced, game, "Constructor")[0] == [CONSTRUCTOR]
    seeds, subject = resolve_seeds(traced, game, "label:Constructor")
    assert seeds == [SMELTER]
    assert subject == "factory 'Constructor' (1 machines)"
    with pytest.raises(SelectorError):
        resolve_seeds(traced, game, "label:no such factory")
