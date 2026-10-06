"""Every response model publishes under its bare class name.

Two routers that each declare a model with the same class name make FastAPI qualify both
with their module path, and ``api/shapes.ts`` loses the short alias it reads them by.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")


def test_no_component_name_is_module_qualified(game):
    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    names = app.openapi()["components"]["schemas"]
    qualified = sorted(name for name in names if name.startswith("satisfactory_mcp__"))
    assert not qualified, f"two models share a class name: {qualified}"
