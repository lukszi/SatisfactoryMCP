"""An argument a tool does not declare is refused, on every tool, before the tool runs.

FastMCP validates arguments against a model that ignores unknown keys, so ``diff_vs_save``
given plan_factory's ``sloops=`` answered for a plant without them and said nothing.
``app.StrictFastMCP`` overrides FastMCP's ``call_tool``; the wire tests below go through a
real client session, so an mcp upgrade that routes calls past the override fails here.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from satisfactory_mcp import server as srv
from satisfactory_mcp.interfaces.mcp.app import undeclared_refusal


def _over_the_wire(name: str, arguments: dict):
    async def call():
        async with create_connected_server_and_client_session(srv.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


def _text(result) -> str:
    assert not result.isError, result
    return "\n".join(block.text for block in result.content)


def test_every_tool_refuses_an_argument_it_does_not_declare():
    async def refusals():
        tools = await srv.mcp.list_tools()
        return {t.name: await srv.mcp.call_tool(t.name, {"no_such_argument": 1}) for t in tools}

    answers = asyncio.run(refusals())
    assert answers
    for name, content in answers.items():
        assert content[0].text.startswith(f"! {name} does not take no_such_argument="), name


def test_the_refusal_reaches_a_client_and_the_tool_never_runs(monkeypatch):
    from satisfactory_mcp.interfaces.mcp import app

    def no_save(*_args, **_kwargs):
        raise AssertionError("the tool ran")

    monkeypatch.setattr(app, "load_world", no_save)
    out = _text(
        _over_the_wire("diff_vs_save", {"objective": "max_mw", "sloops": 2, "required": ["x"]})
    )
    assert out == (
        "! diff_vs_save does not take required=, sloops=; nothing ran. A saved plan carries "
        "required=, sloops=: save it with plan_factory(..., save_as=<name>), then pass "
        "plan=<name> here."
    )


def test_a_declared_call_still_reaches_the_tool_over_the_wire():
    assert _text(_over_the_wire("settings", {})).startswith("# shared settings v0")


def test_a_misspelt_argument_is_answered_with_the_spelling_it_resembles():
    out = undeclared_refusal("search_items", {"query": "iron", "limt": 3}, _declared())
    assert out == "! search_items does not take limt=; nothing ran. Did you mean limit= for limt=?"


@pytest.mark.parametrize(
    ("declared", "hinted"),
    [
        (frozenset({"objective", "plan"}), True),
        (frozenset({"query", "limit"}), False),
    ],
)
def test_only_a_tool_that_recalls_plans_points_at_a_saved_one(declared, hinted):
    out = undeclared_refusal("tool", {"sloops": 2}, declared)
    assert ("A saved plan carries sloops=" in out) is hinted


def test_an_argument_no_plan_stores_gets_no_saved_plan_hint():
    out = undeclared_refusal("diff_vs_save", {"logistics_items": ["Plastic"]}, _diff_args())
    assert "saved plan" not in out


def test_a_call_naming_only_declared_arguments_is_not_refused():
    assert undeclared_refusal("search_items", {"query": "iron", "limit": 3}, _declared()) == ""


def _declared() -> frozenset[str]:
    return asyncio.run(srv.mcp.declared_arguments("search_items"))


def _diff_args() -> frozenset[str]:
    return asyncio.run(srv.mcp.declared_arguments("diff_vs_save"))
