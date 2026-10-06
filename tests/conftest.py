from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.loader import load_docs
from satisfactory_mcp.core.gamedata.normalize import normalize
from satisfactory_mcp.core.saveio.projection import SaveError
from satisfactory_mcp.domain.session import journal
from satisfactory_mcp.domain.world.state import WorldState

FIXTURES = Path(__file__).parent / "fixtures"

#: The repository root, put on ``sys.path`` so that ``tools`` imports as the package it is.
#:
#: ``satisfactory_mcp`` and ``pioneersav`` are installed into the environment; ``tools/`` is
#: deliberately not -- the generators read the reader's own game install and are never
#: shipped in the wheel -- and pytest puts only ``tests/`` on the path. The tests that hold a
#: generator against the server it feeds therefore used to load those files BY PATH, which
#: executes the module afresh on every call and lands a second copy of it in the process
#: beside the ``tools.gen_map_image`` that ``tools/gen_map_renders.py`` imports by name.
#: One line here buys ``from tools import gen_map_image`` instead, in every test module.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


#: The tests that walk the reader's whole save folder, hoisted to the front of the run.
#:
#: **Why order suddenly matters.** ``addopts`` carries ``-n 8``, so the suite runs on eight
#: worker processes and the wall clock is whenever the LAST one finishes. ``xdist``'s default
#: ``load`` scheduler has no idea how long anything takes: it deals tests out in collection
#: order, which is alphabetical by filename, and the three whole-folder tests live in
#: ``test_savparse_parity``, ``test_savparse_trailers`` and ``test_sidecar_placed`` -- all in
#: the last third of the alphabet. Dealt in that order the longest test in the suite starts
#: when the run is already most of the way done, and every other worker then sits idle
#: waiting for it.
#:
#: **The win is real and it is small, and both halves of that are worth recording**, because
#: the obvious version of this reasoning predicts a much bigger one. Paired runs on the
#: integration set, alternating, at the settings this suite actually ships: 26.05 / 26.58 /
#: 25.87 s dealt alphabetically against 25.35 / 25.36 / 25.17 s dealt whole-folder first.
#: Three out of three, about 0.9 s, and it is free. It is not the ten seconds a "the longest
#: test starts last" story would promise -- because these three fan out internally now
#: (``tests/_pool.py``), so the longest of them is ~12 s of a ~25 s run rather than the 88 s
#: it once was, and a tail that short overlaps the rest of the suite either way.
#:
#: **Hoisting first was tried at the WRONG fan-out and looked like a regression**: with the
#: whole-folder tests fanning out 8 wide it measured 31.7 / 32.8 / 33.1 s against 30.0 s
#: alphabetical, because starting all three at once put 3x8 children plus 8 workers on the
#: machine at the moment every worker was also booting and collecting. The order was not the
#: problem; the width was. Recorded because the natural next edit is to re-narrow the fan-out
#: and this ordering would then be a pessimisation again.
#:
#: **Why a hook rather than ``--dist loadgroup``.** Grouping controls WHICH worker a test
#: lands on, and nothing here needs that -- these three share no state and each already fans
#: out internally. What the run needs is for the long pole to be raised FIRST, which is a
#: question about order, not about placement. Measured at ``-n 8``: ``load`` 30.0 s,
#: ``worksteal`` 29.6 s, ``loadfile`` 28.3 s -- a spread inside this machine's run-to-run
#: noise, because no scheduler can recover from starting the longest test last. Reordering is
#: the fix; the scheduler stays the default.
#:
#: Deterministic, and it has to be: every ``xdist`` worker collects independently and they
#: must agree item for item, so this is a stable partition of the list rather than a sort on
#: anything measured at runtime.
WHOLE_FOLDER = "whole_folder"


def pytest_collection_modifyitems(items):
    """Deal the ``whole_folder`` tests first, keeping every other test's relative order."""
    hoisted = [i for i in items if i.get_closest_marker(WHOLE_FOLDER)]
    if hoisted:
        rest = [i for i in items if not i.get_closest_marker(WHOLE_FOLDER)]
        items[:] = hoisted + rest


#: The node field every planner test in this suite plans over, and the reason it is a BOX.
#:
#: It used to be ``["region:Spire Coast"]``, and the reference plans -- 99,729.62 MW over 787
#: buildings, table B's 9,200 Fuel on 16 pipes -- were verified by hand against exactly the
#: nodes that selector returned. Then the region layer was re-derived from the game's own
#: ``FGMapAreaTexture``, and the game's Spire Coast turns out to be a 1.6 km2 coastal strip
#: rather than the 8.5 km2 ring the retired wiki trace drew across the whole north: 51 nodes
#: became 18, and 13 crude became 6. Nothing about the planner changed and every number in
#: thirty-three tests moved, which is the definition of a fixture depending on the wrong
#: thing. A region NAME is advisory by design -- ``domain.spatial.regions`` says so in its
#: first paragraph -- and a regression suite must not be built on one.
#:
#: So the field is stated geographically, once, and can never move again. The box is the
#: bounding box of the nodes the retired selector returned, in metres. It holds all 51 of
#: them and 17 more: 8 Limestone, 5 Iron, 2 Copper, 2 Raw Quartz -- measured, and every one
#: of them irrelevant to these plans, which maximise MW out of crude and coal and export
#: Plastic and Rubber. No extra crude, no extra coal, no extra nitrogen, no extra water. That
#: is why the hand-verified numbers reproduce unchanged over it rather than being re-baselined
#: against whatever the software now says, which would have thrown away the human measurement
#: the whole regression rests on.
#:
#: Each edge is 1 cm outside that bounding box rather than ON it. The box was first drawn
#: to the node table's coordinates at a time when the table stored whole centimetres, and
#: all four extreme nodes therefore sat exactly on an edge -- so when the table went
#: first-party and gained sub-centimetre positions, two of the four drifted 0.2-0.4 cm
#: across the line and the reference field silently shrank by two nodes. The centimetre of
#: margin makes the box mean the field, not one table's rounding; the nearest node outside
#: the field is metres away, so nothing new can fall in.
REFERENCE_FIELD = ("bbox:-649.64,-3140.10,2465.03,-1080.29",)


def _docs_available() -> bool:
    """Whether this machine has the game data, asked at COLLECTION time.

    ``docs_path`` raises when it finds nothing, which is what a tool call wants and is the
    one thing a skip predicate must not do: raising here fails the whole run at import
    instead of skipping the tests that need an install.
    """
    try:
        return config.docs_path().is_file()
    except FileNotFoundError:
        return False


requires_docs = pytest.mark.skipif(
    not _docs_available(), reason="needs the game install (set SATISFACTORY_DOCS)"
)


@pytest.fixture(autouse=True)
def _no_journal_writer(monkeypatch):
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})


@pytest.fixture(autouse=True)
def _own_shared_settings(monkeypatch, tmp_path):
    """Tools read the shared settings; never the reader's own file."""
    path = tmp_path / "shared-settings" / "settings.json"
    monkeypatch.setattr(config, "settings_path", lambda: path)


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
    """The world as it stands on THIS machine, or a skip -- eight modules wrote this out.

    Not the same thing as the ``state`` fixture below, and the difference is the point:
    ``state`` is the committed projection, frozen, which is what makes most of the suite
    reproducible; ``live`` is whatever save is newest right now, which is what a test
    measuring the tool's real answer has to read.

    So it needs a save, and a clone has none. Eight modules built this fixture by hand and
    none of them guarded the call, so on a machine with the game installed and no ``.sav``
    the suite reported **61 errors** -- an error, not a skip, which reads as the code being
    broken rather than as the machine being unequipped. ``SaveError`` is exactly and only the
    "could not produce a projection" signal, so catching it here converts the one condition
    a clone is actually in, and leaves a genuine parser fault raising.

    Function-scoped, matching the fixtures it replaces. It costs nothing to re-enter: the
    projection is cached in-process by ``load_projection`` and this is a dictionary lookup
    after the first test in a session pays the parse.
    """
    from satisfactory_mcp.interfaces.mcp.app import _state

    try:
        return _state(None, None)
    except SaveError as exc:
        pytest.skip(f"needs a readable save: {exc}")


#: What ``save_projection.json`` was cut from, so that re-cutting it is a recipe rather than
#: a reconstruction. Both halves are read straight out of the fixture's own ``header`` block,
#: which is where the next reader should check them rather than trusting this comment.
FIXTURE_SAVE = "Han Solo_280726-230847.sav"
FIXTURE_WORLD = "X2faPVKjX06VaRzClNv5KQ"


@pytest.fixture(scope="session")
def projection() -> dict:
    """The committed sidecar projection: no game install and no .sav needed.

    1.4 MB of one real save, committed because the 2.9 MB ``.sav`` is not, and it is what
    makes most of this suite reproducible on a machine with neither the game nor a save.

    HOW TO REGENERATE IT. One command, run from the repository root, writing over the file::

        uv run python -m satisfactory_mcp.core.saveio.extract \\
            "<saves>/Han Solo_280726-230847.sav" > tests/fixtures/save_projection.json

    ``-m``, not a file path: that is how ``projection._run_sidecar`` invokes it
    in production, so the fixture is cut by exactly the code path the server uses. There is no
    flag and no post-processing -- the sidecar's stdout IS the fixture.

    FROM THE SAME WORLD, and this is the part that is not a formality. The save above is
    ``save_identifier`` ``X2faPVKjX06VaRzClNv5KQ`` ("Han Solo"), and a projection cut from any
    other world is a different factory: different machine counts, different node occupancy,
    different graph. A dozen modules in ``src`` justify a design decision by quoting a number
    measured on THIS fixture, and ``test_reference_counts.py`` exists solely to fail loudly
    when a re-cut moves one -- so a regeneration from a newer save of the same world is a
    normal thing to do and is expected to break that file, while a regeneration from a
    different world silently invalidates the reasoning rather than the numbers.

    So, after re-cutting: run ``uv run pytest -q``, expect ``test_reference_counts.py`` to
    fail, and update both the assertion there and the comment in ``src`` it names. Both.

    The world rule is asserted rather than only written down, because the failure it guards
    against does not look like a failure -- a fixture from another world produces a suite that
    fails in twenty places for twenty apparently unrelated reasons, and this says the one
    thing that explains all of them. The FILENAME is deliberately not asserted: a newer save
    of the same world is the ordinary, expected regeneration.
    """
    body = json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))
    found = (body.get("header") or {}).get("save_identifier")
    assert found == FIXTURE_WORLD, (
        f"save_projection.json was cut from world {found!r}, not {FIXTURE_WORLD!r} -- this "
        f"suite measures one factory, and the reference save is {FIXTURE_SAVE}. Re-cut it "
        "from that world, or change both constants in conftest.py and re-measure every count "
        "test_reference_counts.py pins."
    )
    return body


@pytest.fixture(scope="session")
def state(game, projection) -> WorldState:
    return WorldState(projection=projection, game=game)


#: The stored arguments of the planner's ``spire-coast-full``, the decoupled three-module plan.
SPIRE_COAST_FULL = {
    "objective": "max_mw",
    "sources": ["region:Spire Coast"],
    "exports": ["MW", "Plastic", "Rubber"],
    "export_minimums": {"Plastic": 600.0, "Rubber": 250.0},
    "only_free_nodes": False,
    "allow_sinks": True,
    "extractor_clocks": [1.0, 1.5, 2.0, 2.5],
    "machine_cost_mw": 5.0,
    "exclude_recipes": [
        "Turbofuel",
        "Alternate: Compacted Coal",
        "Coal-Powered Generator",
        "Alternate: Recycled Plastic",
        "Alternate: Recycled Rubber",
    ],
    "water_extractors": 64,
}


@pytest.fixture
def planned(monkeypatch, tmp_path, projection, game) -> WorldState:
    """The fixture world with ``spire-coast-full`` saved in a private plan store.

    The planning tools read it too, through a new state per call as the server builds one,
    so a plan one call saves is seen by the next.
    """
    from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
    from satisfactory_mcp.interfaces.mcp.tools import planning

    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path)
    PlanLog(FIXTURE_WORLD).create("spire-coast-full", SPIRE_COAST_FULL, actor=Actor("chat"))

    def fresh(save=None, world=None, as_of=None):
        return WorldState(projection=projection, game=game)

    monkeypatch.setattr(planning, "_state", fresh)
    return fresh()


@pytest.fixture
def labelled(game, projection, tmp_path, monkeypatch) -> WorldState:
    """The fixture world with its own factory names, read from a private label store.

    ``tier 1&2`` and ``steel factory`` are the reference world's labels, so a test that names
    them reads this copy rather than whatever the machine's label store holds. A fresh state,
    because ``state`` is shared and caches the labels it saw first.
    """
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / f"{FIXTURE_WORLD}.json").write_bytes(
        (FIXTURES / "labels_reference.json").read_bytes()
    )
    monkeypatch.setattr(config, "labels_dir", lambda: labels)
    return WorldState(projection=projection, game=game)


def _explode(save=None, world=None):
    """A loader that fails the way the real one fails when the sidecar produces nothing."""
    raise RuntimeError("sidecar produced no output")


@pytest.fixture
def client(state, game):
    """The API over the fixture world. Both loaders are stubs; no save is ever read.

    Here rather than in one test module because the web surface is split across a file per
    router and every one of them needs this exact app. ``fastapi`` is imported INSIDE the
    fixture, not at the top of this file: it lives in the optional ``web`` extra and this
    conftest is imported by the whole suite, so a module-level import would turn "the extra
    is not installed" into a collection error for tests that never touch HTTP. The web test
    modules ``importorskip`` it themselves, so anything that reaches this fixture has one.
    """
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: state,
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def labelled_client(labelled, game):
    """The ``client`` app over ``labelled``, the fixture world with its factory names."""
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web.app import create_app

    app = create_app(
        state_loader=lambda save=None, world=None: labelled,
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c
