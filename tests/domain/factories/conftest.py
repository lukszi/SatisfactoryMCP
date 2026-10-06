"""The synthetic two-site world the factory-graph tests share (``tests.support.synthetic_factories``)."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from tests.support.synthetic_factories import two_site_projection


@pytest.fixture(scope="module")
def synthetic_projection() -> dict:
    return two_site_projection()


@pytest.fixture(scope="module")
def synthetic_graph(synthetic_projection):
    return build_graph(synthetic_projection)
