"""Suite-wide fixtures: the game data, the committed reference world, and private user data.

docs/DEVELOPING.md ("Test suite") explains the reference world, how its projection is re-cut,
and why the whole-folder tests are dealt first.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.loader import load_docs
from satisfactory_mcp.core.gamedata.normalize import normalize
from satisfactory_mcp.core.saveio.projection import SaveError
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.map_jobs import in_use_local, local, runner  # noqa: F401  (fixtures)
from tests.support.paths import FIXTURES
from tests.support.reference_world import FIXTURE_SAVE, FIXTURE_WORLD, SPIRE_COAST_FULL
from tests.support.web import client_over

#: Marks the tests that parse every save on the machine; they are dealt to workers first.
WHOLE_FOLDER = "whole_folder"

#: The ``config`` paths under the user data root, each cached after its first call. Held as
#: the functions themselves, so a test that patches one cannot hide its cache from a clear.
USER_DATA_PATHS = (
    config.plans_dir,
    config.labels_dir,
    config.activity_dir,
    config.ui_dir,
    config.pins_dir,
    config.asks_dir,
    config.advice_dir,
    config.settings_path,
)


def pytest_collection_modifyitems(items):
    """Deal the ``whole_folder`` tests first, keeping every other test's relative order."""
    hoisted = [item for item in items if item.get_closest_marker(WHOLE_FOLDER)]
    if hoisted:
        rest = [item for item in items if not item.get_closest_marker(WHOLE_FOLDER)]
        items[:] = hoisted + rest


def _docs_available() -> bool:
    """Whether this machine has the game data; never raises, so it can gate a skip."""
    try:
        return config.docs_path().is_file()
    except FileNotFoundError:
        return False


def _clear_user_data_caches() -> None:
    for cached in USER_DATA_PATHS:
        cached.cache_clear()


@pytest.fixture(autouse=True)
def _private_user_data(tmp_path):
    """Every store a test writes lives under its own ``tmp_path``, never the reader's.

    Its own patch rather than ``monkeypatch``, so a test's ``monkeypatch.undo()`` keeps it.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SATISFACTORY_USER_DATA", str(tmp_path / "user"))
        _clear_user_data_caches()
        yield
    _clear_user_data_caches()


@pytest.fixture
def user_data(tmp_path):
    """The private user data root ``_private_user_data`` points every store at."""
    return tmp_path / "user"


@pytest.fixture(autouse=True)
def _no_journal_writer(monkeypatch):
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})


@pytest.fixture(scope="session", autouse=True)
def _own_scarcity_tiers(tmp_path_factory):
    """Pricing writes the last scarcity tiers per world; never into the reader's plans.
    Session-wide, because module fixtures price the save before any function fixture runs."""
    from satisfactory_mcp.domain.planning.solver import prices

    root = tmp_path_factory.mktemp("tiers")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(prices, "tiers_path", lambda world: root / f"{world}.json")
        yield


@pytest.fixture(scope="session")
def game():
    if not _docs_available():
        pytest.skip("needs the game install")
    return normalize(load_docs(config.docs_path()))


@pytest.fixture
def live(game) -> WorldState:
    """The newest save on this machine, or a skip when there is none to read."""
    from satisfactory_mcp.domain.world.state import load_state
    from satisfactory_mcp.interfaces.mcp import app

    try:
        return load_state(app.game(), path=None, world=None)
    except SaveError as exc:
        pytest.skip(f"needs a readable save: {exc}")


@pytest.fixture(scope="session")
def projection() -> dict:
    """The committed sidecar projection of the reference save: no game install, no .sav."""
    body = json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))
    found = (body.get("header") or {}).get("save_identifier")
    assert found == FIXTURE_WORLD, (
        f"save_projection.json was cut from world {found!r}, not {FIXTURE_WORLD!r} -- this "
        f"suite measures one factory, and the reference save is {FIXTURE_SAVE}. Re-cut it "
        "from that world, or change both constants in tests/support/reference_world.py and "
        "re-measure every count tests/data/test_reference_counts.py pins."
    )
    return body


@pytest.fixture(scope="session")
def state(game, projection) -> WorldState:
    return WorldState(projection=projection, game=game)


@pytest.fixture
def use_world(monkeypatch):
    """Serve every MCP tool a world of the test's choosing instead of reading a save.

    ``use_world(state)`` answers every call with that state; ``use_world(make)`` calls
    ``make()`` per call, for a fresh state each time as the server builds one.
    """
    from satisfactory_mcp.interfaces.mcp import app

    def serve(chosen) -> None:
        def load_world(save=None, world=None, as_of=None, **_):
            return chosen() if callable(chosen) else chosen

        monkeypatch.setattr(app, "load_world", load_world)

    return serve


@pytest.fixture
def planned(monkeypatch, projection, game, use_world) -> WorldState:
    """The fixture world with ``spire-coast-full`` saved in the private plan store.

    Every tool reads it, through a new state per call as the server builds one, so a plan
    one call saves is seen by the next.
    """
    from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog

    PlanLog(FIXTURE_WORLD).create("spire-coast-full", SPIRE_COAST_FULL, actor=Actor("chat"))

    def fresh():
        return WorldState(projection=projection, game=game)

    use_world(fresh)
    return fresh()


@pytest.fixture
def labelled(game, projection) -> WorldState:
    """The fixture world with its own factory names, read from the private label store.

    A fresh state, because ``state`` is shared and caches the labels it saw first.
    """
    (config.labels_dir() / f"{FIXTURE_WORLD}.json").write_bytes(
        (FIXTURES / "labels_reference.json").read_bytes()
    )
    return WorldState(projection=projection, game=game)


@pytest.fixture
def client(state, game):
    """The API over the shared fixture world; no save is ever read."""
    with client_over(state, game) as test_client:
        yield test_client


@pytest.fixture
def labelled_client(labelled, game):
    """The API over ``labelled``, the fixture world with its factory names."""
    with client_over(labelled, game) as test_client:
        yield test_client


@pytest.fixture
def fresh_state_client(projection, game):
    """The API over a new fixture world per request, as the server builds one per call."""
    with client_over(
        lambda save=None, world=None: WorldState(projection=projection, game=game), game
    ) as test_client:
        yield test_client


@pytest.fixture
def stateless_client():
    """The API with no world and no game data: the surface that needs neither."""
    with client_over(None, None) as test_client:
        yield test_client
