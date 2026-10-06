"""Every response model publishes under its bare class name, and every shape under one name.

Two routers that each declare a model with the same class name make FastAPI qualify both
with their module path, and ``api/shapes.ts`` loses the short alias it reads them by. Two
models with the same fields are one shape published twice, free to drift apart (wire rule 5
of docs/web-wire.md).
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

fastapi = pytest.importorskip("fastapi")


def _components(game) -> dict:
    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    return app.openapi()["components"]["schemas"]


def _shape(node: object) -> object:
    """A schema without the words around it: what two names would have to share."""
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items() if k not in ("title", "description")}
    if isinstance(node, list):
        return [_shape(v) for v in node]
    return node


def test_no_component_name_is_module_qualified(game):
    names = _components(game)
    qualified = sorted(name for name in names if name.startswith("satisfactory_mcp__"))
    assert not qualified, f"two models share a class name: {qualified}"


def test_no_shape_is_published_under_two_names(game):
    by_shape = defaultdict(list)
    for name, schema in _components(game).items():
        by_shape[json.dumps(_shape(schema), sort_keys=True)].append(name)
    twins = sorted(names for names in by_shape.values() if len(names) > 1)
    assert not twins, f"merge these into one shape under one name: {twins}"
