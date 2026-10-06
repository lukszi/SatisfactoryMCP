"""A stored plan's arguments as JSON: ``PlanArgs.to_dict`` lists its fields by hand."""

from __future__ import annotations

from dataclasses import fields

from satisfactory_mcp.domain.planning.stored.plan_args import PlanArgs
from satisfactory_mcp.domain.planning.stored.views import PlanArgsBody


def test_every_field_reaches_the_dict_in_declaration_order():
    names = [f.name for f in fields(PlanArgs)]
    assert list(PlanArgs().to_dict()) == names
    assert list(PlanArgsBody.__annotations__) == names


def test_the_dict_is_a_copy():
    args = PlanArgs(sources=["region:Spire Coast"], supplied={"Desc_Water_C": 60.0})
    out = args.to_dict()
    out["sources"].append("node:x")
    out["supplied"]["Desc_Water_C"] = 0.0
    assert args.sources == ["region:Spire Coast"] and args.supplied == {"Desc_Water_C": 60.0}
