"""The MCP surface itself: registration, and the context-budget invariants.

These are cheap and catch two classes of silent regression -- a tool whose schema
stops enforcing its row cap, and a response that quietly grows.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from satisfactory_mcp import server as srv
from tests.support.reference_world import REFERENCE_FIELD

#: ``usefixtures("game")`` is the guard, not decoration. Every test here calls a tool, and a
#: tool reaches the game data through ``app.game()`` -- an lru_cache'd function, not this
#: suite's fixture -- so with no ``Docs.json`` on the machine they raised FileNotFoundError
#: out of the middle of a call rather than skipping. Requesting ``game`` puts the suite's own
#: "is the install here" check in front of every one of them; it is session-scoped, so on a
#: machine that has the install it costs one dictionary lookup per test.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("game")]

#: Per-response ceiling in characters. Generous, but a tool that blows past it is
#: almost certainly returning a whole table.
BUDGET = 4000


def _run(coro):
    return asyncio.run(coro)


def test_tools_resources_prompts_registered():
    tools = _run(srv.mcp.list_tools())
    resources = _run(srv.mcp.list_resources())
    prompts = _run(srv.mcp.list_prompts())
    assert len(tools) >= 19
    names = {t.name for t in tools}
    for expected in (
        "search_resource_nodes",
        "rank_build_sites",
        "plan_factory",
        "diff_vs_save",
        "list_regions",
        "advise_hard_drive_pick",
    ):
        assert expected in names, expected
    assert {str(r.uri) for r in resources} == {
        "satisfactory://docs/summary",
        "satisfactory://save/current",
        "satisfactory://map/regions",
        # Player-authored labels, published as an interface rather than kept private.
        "satisfactory://factories/labels",
    }
    assert {p.name for p in prompts} == {
        "design_factory",
        "plan_power_plant",
        "pick_hard_drive",
    }


def test_no_tool_advertises_structured_output():
    """A tool annotated -> str otherwise duplicates its whole payload into
    structuredContent, a measured ~1.96x wire-size tax."""
    for tool in _run(srv.mcp.list_tools()):
        assert not tool.outputSchema, tool.name


def test_row_limits_are_schema_enforced():
    """The model must not be able to ask for 291 rows."""
    for tool in _run(srv.mcp.list_tools()):
        limit = (tool.inputSchema.get("properties") or {}).get("limit")
        if not limit:
            continue
        flat = str(limit)
        assert "25" in flat, f"{tool.name} limit is not capped: {limit}"


def test_every_declared_default_lies_inside_its_own_bound():
    """Pydantic does not validate defaults, so a tool can publish one its own schema
    rejects -- and a client that echoes the advertised default back gets a hard error for
    doing exactly what the schema told it. commission_plan shipped `limit=60` against
    `le=25` for that reason."""
    for tool in _run(srv.mcp.list_tools()):
        for name, spec in (tool.inputSchema.get("properties") or {}).items():
            default = spec.get("default")
            if not isinstance(default, int | float) or isinstance(default, bool):
                continue
            lo, hi = spec.get("minimum"), spec.get("maximum")
            assert lo is None or default >= lo, (tool.name, name, default, lo)
            assert hi is None or default <= hi, (tool.name, name, default, hi)


def test_no_response_names_an_offset_the_schema_would_reject():
    """Fifteen tools printed "call again with offset=N" and took no offset, so the one
    instruction the envelope gives was an error every time it was followed.

    The guard is at the primitive rather than per tool: ``render.table`` only writes that
    sentence when the caller passes ``offset=``, and passing it from a tool without the
    parameter is the mistake this catches. A truncated table that cannot page says how to
    narrow instead, so this test does not forbid truncation -- only lying about it."""
    import inspect

    for tool in _run(srv.mcp.list_tools()):
        fn = getattr(srv, tool.name, None)
        if fn is None:
            continue
        src = inspect.getsource(inspect.unwrap(fn))
        if "offset=" not in src:
            continue
        assert "offset" in (tool.inputSchema.get("properties") or {}), (
            f"{tool.name} pages a table but publishes no offset parameter"
        )


def test_tool_descriptions_stay_short():
    """Tool descriptions are always resident, so their first line is the budget that
    matters. Procedure belongs in prompts."""
    for tool in _run(srv.mcp.list_tools()):
        first = (tool.description or "").strip().splitlines()[0]
        assert len(first) <= 120, (tool.name, first)


def test_ui_context_takes_answered_asks_without_a_new_tool():
    tools = {t.name: t for t in _run(srv.mcp.list_tools())}
    props = tools["ui_context"].inputSchema["properties"]
    assert set(props) == {"save", "world", "answered", "dismissed"}
    assert props["answered"]["description"] == (
        'ask:N ids you have answered, each may add a line: "ask:7 <answer>"'
    )
    assert props["dismissed"]["description"] == (
        'adv: ids to hide on the page; "adv:3f9a snooze" hides for 1 h of play'
    )
    assert "ask:" in srv.mcp.instructions and "pin:" in srv.mcp.instructions
    first = tools["ui_context"].description.strip().splitlines()[0]
    assert len(first) <= 120


def test_show_on_map_pins_with_a_flag_rather_than_a_tool():
    tools = {t.name: t for t in _run(srv.mcp.list_tools())}
    pin = tools["show_on_map"].inputSchema["properties"]["pin"]
    assert pin["default"] is False and "pin:N" in pin["description"]
    assert "pin" not in tools


def test_alternates_for_item_takes_a_plan_without_a_second_tool():
    tools = {t.name: t for t in _run(srv.mcp.list_tools())}
    plan = tools["alternates_for_item"].inputSchema["properties"]["plan"]
    assert "stored plan" in json.dumps(plan)
    assert [n for n in tools if "alternate" in n or "swap" in n] == ["alternates_for_item"]
    first = tools["alternates_for_item"].description.strip().splitlines()[0]
    assert len(first) <= 120


def test_describe_location_declares_one_way_to_say_where():
    """It declared `x_m`/`y_m` AND `at=`, which strictly subsumes them, so a client reading
    the schema met two spellings of one thing and had to guess which the tool preferred."""
    tool = next(t for t in _run(srv.mcp.list_tools()) if t.name == "describe_location")
    assert set(tool.inputSchema["properties"]) == {"at", "radius_m", "save", "world", "as_of"}


def test_the_finder_tools_take_the_status_and_network_the_page_filters_by():
    """``status`` and ``network`` are the World page's filters, so chat can ask the same."""
    tools = {t.name: t for t in _run(srv.mcp.list_tools())}
    assert "status" in tools["search_resource_nodes"].inputSchema["properties"]
    assert "only_free" in tools["search_resource_nodes"].inputSchema["properties"]
    assert "network" in tools["search_conduits"].inputSchema["properties"]


@pytest.mark.parametrize(
    "uri",
    [
        "satisfactory://docs/summary",
        "satisfactory://save/current",
        "satisfactory://map/regions",
    ],
)
def test_resources_are_small(uri):
    out = _run(srv.mcp.read_resource(uri))
    text = out[0].content if isinstance(out, list) else str(out)
    assert 0 < len(text) < 1000


def test_docs_summary_reports_no_normalisation_warnings():
    out = _run(srv.mcp.read_resource("satisfactory://docs/summary"))
    text = out[0].content if isinstance(out, list) else str(out)
    assert "warnings=0" in text


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("search_items", {"query": "fuel"}),
        ("search_recipes", {"query": "turbo"}),
        ("alternates_for_item", {"item": "Plastic"}),
        ("list_buildings", {"kind": "generator"}),
        ("list_regions", {}),
        ("describe_location", {"at": "239,-1928"}),
        ("world_summary", {}),
        ("power_report", {}),
        ("unlocked_recipes", {}),
        ("factory_sites", {"limit": 5}),
        ("search_conduits", {"near": "239,-1928"}),
        ("search_resource_nodes", {"sources": ["north"], "resource": "Crude Oil"}),
        ("rank_build_sites", {"resource": "Crude Oil", "sources": ["north"]}),
        ("plan_factory", {"objective": "max_mw", "sources": list(REFERENCE_FIELD)}),
        (
            "diff_vs_save",
            {
                "objective": "max_mw",
                "sources": list(REFERENCE_FIELD),
                "exports": ["MW", "Plastic", "Rubber"],
            },
        ),
        ("list_pending_hard_drive_choices", {}),
        ("advise_hard_drive_pick", {"hard_drive_id": 34}),
    ],
)
def test_response_fits_the_context_budget(name, kwargs):
    out = getattr(srv, name)(**kwargs)
    assert isinstance(out, str)
    assert out.strip()
    assert len(out) < BUDGET, f"{name} returned {len(out)} chars"


# --------------------------------------------------------- logistics rows

#: The plan that found the bug: plastic and rubber are the whole point of it, and both
#: sit near the bottom of a flow table ranked by volume.
OIL_PLAN = dict(
    objective="max_mw",
    sources=list(REFERENCE_FIELD),
    exports=["MW", "Plastic", "Rubber"],
    export_minimums={"Plastic": 300.0, "Rubber": 300.0},
)


def _logistics_items(text: str) -> list[str]:
    """Item column of the logistics table, in order."""
    block = text[text.index("# logistics") :].splitlines()[2:]
    return [line.split("\t")[0] for line in block if "\t" in line]


def test_logistics_honours_the_limit_it_is_given():
    """It ignored `limit` entirely and truncated at a hardcoded 6."""
    assert len(_logistics_items(srv.plan_factory(limit=3, **OIL_PLAN))) == 3
    assert len(_logistics_items(srv.plan_factory(limit=12, **OIL_PLAN))) > 6


def test_a_named_item_survives_truncation_by_volume():
    """THE correction. Plastic is 7th by volume in this plan, so at the old cap of 6
    it was invisible -- and it is one of the two items the plan exists to size."""
    unpinned = _logistics_items(srv.plan_factory(limit=6, **OIL_PLAN))
    assert "Plastic" not in unpinned

    pinned = _logistics_items(srv.plan_factory(limit=3, logistics_items=["Plastic"], **OIL_PLAN))
    assert pinned[0] == "Plastic"


def test_pinning_never_costs_a_row_that_was_already_shown():
    """Pinning is additive: asking for two small items must not push two big ones out,
    or the fix would trade one blind spot for another."""
    plain = _logistics_items(srv.plan_factory(limit=6, **OIL_PLAN))
    pinned = _logistics_items(
        srv.plan_factory(limit=6, logistics_items=["Plastic", "Rubber"], **OIL_PLAN)
    )
    assert set(plain) <= set(pinned)


def test_an_unknown_logistics_item_is_reported_not_ignored():
    out = srv.plan_factory(limit=6, logistics_items=["Nonsuch"], **OIL_PLAN)
    assert "no item matches 'Nonsuch'" in out


def test_truncated_logistics_says_how_many_it_hid():
    out = srv.plan_factory(limit=3, **OIL_PLAN)
    assert "showing 3 of" in out


def test_an_unknown_export_token_is_refused_by_name():
    """Four INFEASIBLE calls came out of a mangled export whitelist, so the tool now
    refuses rather than solving a question nobody asked."""
    out = srv.plan_factory(objective="max_mw", sources=list(REFERENCE_FIELD), exports=["Plastik"])
    assert "Plastik" in out
    assert "REPLACES the default" in out


# --------------------------------------------------------- a named export solved to zero


def test_a_named_export_solved_to_zero_is_said_out_loud():
    """A session asked for Plastic and Rubber, got 766 Plastic and 0 Rubber, and nothing
    said so -- exports is a whitelist and the zero was a legal optimum, but it answered a
    question the caller did not ask. The zero now leads the notes AND appears on the
    exports line, where a caller checks what they asked for."""
    out = srv.plan_factory(
        objective="max_item",
        target_item="Plastic",
        sources=list(REFERENCE_FIELD),
        exports=["Plastic", "Rubber"],
    )
    assert "EXPORT AT ZERO: Rubber" in out
    assert "Rubber=0" in out
    # And the way out is named: only export_minimums makes a whitelisted item leave.
    assert "export_minimums={'Rubber': <rate>}" in out


def test_a_zero_export_names_the_intermediate_that_ate_it():
    """Where the LP can tell WHY, it says so in one sentence: under max_mw every drop of
    fuel is worth more burned than exported, so all of it is consumed inside the plan."""
    out = srv.plan_factory(
        objective="max_mw", sources=list(REFERENCE_FIELD), exports=["MW", "Fuel"]
    )
    assert "EXPORT AT ZERO: Fuel" in out
    assert "consumed inside the plan as an intermediate" in out


def test_a_satisfied_export_raises_no_alarm():
    """The warning must mean something: a plan whose named exports all flow stays quiet."""
    out = srv.plan_factory(**OIL_PLAN)
    assert "EXPORT AT ZERO" not in out


def test_prompts_render_with_arguments():
    res = _run(
        srv.mcp.get_prompt("design_factory", {"target_item": "Rubber", "rate_per_min": "120"})
    )
    text = "\n".join(m.content.text for m in res.messages)
    assert "Rubber" in text
    assert "120" in text
    assert "INFEASIBLE" in text  # the byproduct escape hatch must be spelled out


def test_every_tool_module_is_imported_by_the_package():
    """The imports in tools/__init__.py look unused and are not: each module's
    decorators run on import, and that is what attaches it to the shared `mcp`. A module
    dropped from that list would silently remove its tools -- the server would start
    fine and simply not offer them."""
    import pkgutil

    from satisfactory_mcp.interfaces.mcp import tools

    on_disk = {m.name for m in pkgutil.iter_modules(tools.__path__)}
    assert on_disk, "no tool modules found"
    assert on_disk <= set(tools.__all__), f"not imported: {on_disk - set(tools.__all__)}"


def test_the_registered_surface_survives_the_split():
    """Pinned counts, because the split moved 36 tools between files and a decorator
    that fails to run is invisible. 44: +commission_plan, +mam_research, +rank_unlocks,
    +somersloops, +trace_upstream, +collected_from_world, +search_conduits, +site_plan.
    48: +stock, +storage, +crates, +factory_floors -- the projection keys the map read and
    no tool did. 49: +rename_plan. 50: +milestones, the HUB half of the ladder mam_research
    already walked for the MAM. 52: +rename_factory, +amend_factory -- a label could be
    written and deleted and nothing in between. 54: +plan_log, +ui_context -- plan versions
    and what the page has open. 55: +settings, what the page and chat share."""
    tools = _run(srv.mcp.list_tools())
    assert len(tools) == 55
    assert {
        "amend_factory",
        "collected_from_world",
        "commission_plan",
        "crates",
        "factory_floors",
        "mam_research",
        "milestones",
        "plan_log",
        "rank_unlocks",
        "rename_factory",
        "rename_plan",
        "search_conduits",
        "settings",
        "site_plan",
        "somersloops",
        "stock",
        "storage",
        "trace_upstream",
        "ui_context",
    } <= {t.name for t in tools}
    assert len(_run(srv.mcp.list_resources())) == 4
    assert len(_run(srv.mcp.list_prompts())) == 3


def test_server_still_re_exports_what_callers_reach_for():
    """server.py is now a thin aggregator, but tests and scripts import tools from it by
    name. Losing a re-export breaks them at import time rather than at call time."""
    for name in (
        "mcp",
        "game",
        "PLAN_DEFAULTS",
        "GRAPH_INDEX_WARNING",
        "plan_factory",
        "plan_layout",
        "diff_vs_save",
        "bom",
        "factory_query",
        "factory_labels",
        "list_buildings",
        "search_recipes",
        "search_resource_nodes",
        "_state",
        "_origin_for",
        "_plan_kwargs",
        "_resolve_factory",
    ):
        assert hasattr(srv, name), name


def test_every_registered_tool_is_reachable_by_name_from_the_server():
    """The generalisation of the list above, which is a hand-kept list and therefore drifts.

    Registration and re-export are two separate acts -- the decorator attaches a tool to `mcp`
    on import, and `server.py` names it again for callers -- so a tool can be fully working over
    MCP and still be missing from `import server`. That is invisible to every other test here,
    because a tool the suite exercises through `mcp.list_tools()` is found and a tool the suite
    exercises as `srv.name` is a different code path.
    """
    missing = sorted(t.name for t in _run(srv.mcp.list_tools()) if not hasattr(srv, t.name))
    assert missing == [], missing


def test_no_tool_module_imports_another():
    """Shared helpers live in `app`, so the tool modules stay siblings. One importing
    another is the first step back toward a single file."""
    import pathlib
    import re

    # Asked of the package `server` actually imported, not of a spelled-out path: the
    # tool modules have moved once already, and a stale literal here would not fail --
    # it would glob an empty directory and pass without checking anything.
    root = pathlib.Path(srv._tools.__file__).parent
    for path in root.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        siblings = re.findall(
            r"^\s*from \.(\w+) import|^\s*from \. import (\w+)", text, re.MULTILINE
        )
        found = {a or b for a, b in siblings}
        assert not found, f"{path.name} imports sibling tool module(s): {found}"
