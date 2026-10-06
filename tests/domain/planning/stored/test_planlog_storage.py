"""The plan log on disk: the legacy migration, newer schemas, and two writers at once."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning.stored import planlog
from satisfactory_mcp.domain.planning.stored.planlog import (
    InvalidOp,
    NameTaken,
    PlanLog,
)
from tests.support.plan_log import CHAT, PAGE


def _legacy(world: str = "W") -> Path:
    path = config.plans_dir() / f"{world}.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "version": 4,
                "world_id": world,
                "session_name": "s",
                "plans": [
                    {
                        "name": "spire",
                        "args": {
                            "objective": "max_mw",
                            "sources": ["box:1,2,3,4"],
                            "exclude_recipes": ["Recycled"],
                            "sloops": 2,
                            "retired_knob": 7,
                        },
                        "notes": "typed by hand",
                        "plan_id": "abc12345",
                        "factory": "coast",
                        "created": "x.sav",
                        "provenance": {"selectors": [{"selector": "box:1,2,3,4", "count": 3}]},
                        "siting": {"x_m": 1.0, "y_m": 2.0},
                    },
                    {
                        "name": "old",
                        "args": {"objective": "min_power"},
                        "notes": "",
                        "plan_id": "",
                        "factory": "",
                        "created": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_legacy_plans_migrate_once_without_losing_any():
    legacy = _legacy()
    before = legacy.read_bytes()

    plans = PlanLog("W")
    marker = json.loads((plans.root / "migrated.json").read_text(encoding="utf-8"))
    assert marker["schema"] == 1 and marker["from"] == "W.json"
    assert sorted(marker["keys"]) == ["old", "spire"]

    spire = plans.find("spire")
    assert spire.rev == 1 and spire.key == marker["keys"]["spire"]
    assert spire.args.banned == ["Recycled"]
    assert spire.kwargs() == {
        "sources": ["box:1,2,3,4"],
        "exclude_recipes": ["Recycled"],
        "sloops": 2,
    }
    assert (spire.notes, spire.factory, spire.created, spire.plan_id) == (
        "typed by hand",
        "coast",
        "x.sav",
        "abc12345",
    )
    assert spire.provenance["selectors"][0]["count"] == 3
    assert spire.siting == {"x_m": 1.0, "y_m": 2.0}
    assert plans.commits(spire.key)[0].actor.kind == "migration"
    assert (plans.root / spire.key / "snap" / "1.json").is_file()
    assert legacy.read_bytes() == before, "the legacy file is never modified"

    view = plans.view()
    assert [p.name for p in view.plans] == ["spire", "old"]
    assert view.find("spi").key == spire.key and view.plans[0].rev == 1

    assert PlanLog("W").migrate() == {}
    assert len(PlanLog("W").keys()) == 2


def test_a_migration_interrupted_before_its_marker_does_not_duplicate():
    _legacy()
    plans = PlanLog("W")
    (plans.root / "migrated.json").unlink()
    again = PlanLog("W")
    assert len(again.keys()) == 2
    assert (again.root / "migrated.json").is_file()


def test_no_legacy_file_means_nothing_to_migrate(plans):
    assert plans.migrate() == {} and not (plans.root / "migrated.json").exists()


def test_the_migration_backs_up_the_legacy_file_and_stamps_its_version(monkeypatch):
    monkeypatch.setattr(planlog.schema, "writer_version", lambda: "9.8.7")
    legacy = _legacy()
    before = legacy.read_bytes()
    plans = PlanLog("W")
    backup = plans.root / "backup-v9.8.7" / "W.json"
    assert backup.read_bytes() == before
    marker = json.loads((plans.root / "migrated.json").read_text(encoding="utf-8"))
    assert marker["version"] == "9.8.7"


def test_a_legacy_file_from_a_newer_schema_is_refused_and_nothing_is_written():
    legacy = _legacy()
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    legacy.write_text(json.dumps({**raw, "schema": planlog.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(planlog.schema.NewerSchema, match="newer version"):
        PlanLog("W")
    root = PlanLog.dir_for("W")
    assert not (root / "migrated.json").exists()
    assert not list(root.glob("*/ops.jsonl")) and not list(root.glob("backup-*"))


def test_a_log_migrated_by_a_newer_schema_is_refused():
    _legacy()
    marker = PlanLog("W").root / "migrated.json"
    raw = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**raw, "schema": planlog.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(planlog.schema.NewerSchema):
        PlanLog("W")


def test_every_spelling_of_power_is_stored_as_mw():
    """The migration once stored the solver's ``__MW__``, and the page's "+MW" then added
    a second power export beside it."""
    legacy = _legacy()
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    raw["plans"][0]["args"]["exports"] = ["__MW__", "Plastic"]
    raw["plans"][0]["args"]["export_minimums"] = {"power": 50, "Plastic": 920}
    legacy.write_text(json.dumps(raw), encoding="utf-8")
    plans = PlanLog("W")
    spire = plans.find("spire")
    assert spire.args.exports == ["MW", "Plastic"]
    assert spire.args.export_minimums == {"MW": 50.0, "Plastic": 920.0}
    pushed = plans.push(
        spire.key, 1, [{"op": "add", "field": "exports", "member": "MW"}], actor=PAGE
    )
    assert pushed.noop and plans.find("spire").args.exports == ["MW", "Plastic"]
    plans.push(spire.key, 1, [{"op": "remove", "field": "exports", "member": "power"}], actor=PAGE)
    assert plans.find("spire").args.exports == ["Plastic"]
    assert all(planlog.is_power(x) for x in ("MW", "mw", " Power ", "__MW__"))
    assert not planlog.is_power("Plastic")


def test_an_older_log_that_stored_the_solver_spelling_replays_as_mw(plans):
    key = plans.create("rig", {"exports": ["Plastic"]}, actor=CHAT).key
    ops = plans._ops(key)
    lines = ops.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["ops"][0]["state"]["args"]["exports"] = ["__MW__", "Plastic"]
    ops.write_text(json.dumps(first) + "\n", encoding="utf-8")
    shutil.rmtree(plans.root / key / "snap")
    assert plans.state(key).args.exports == ["MW", "Plastic"]


def test_free_name_is_the_one_check_for_a_plan_name(plans, plan):
    assert plans.free_name("  coast  ") == "coast"
    assert plans.free_name("NORTH HMF", plan) == "NORTH HMF"
    with pytest.raises(NameTaken):
        plans.free_name("NORTH HMF")
    with pytest.raises(InvalidOp, match="cannot be blank"):
        plans.free_name("   ")


_WRITER = """
import sys
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
key, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
log = PlanLog("W")
for i in range(n):
    log.push(key, 1, [{"op": "add", "field": "sources", "member": f"{tag}-{i}"}],
             actor=Actor("chat", tag))
"""


def test_two_processes_writing_at_once_lose_nothing(user_data):
    key = PlanLog("W").create("shared", {}, actor=PAGE).key
    env = {
        **os.environ,
        "SATISFACTORY_USER_DATA": str(user_data),
        "PYTHONPATH": str(Path(planlog.__file__).resolve().parents[5]),
    }
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, key, tag, "25"], env=env)
        for tag in ("a", "b")
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    log = PlanLog("W")
    assert log.head_rev(key) == 51
    assert [c.rev for c in log.commits(key)] == list(range(1, 52))
    assert sorted(log.state(key).args.sources) == sorted(
        [f"a-{i}" for i in range(25)] + [f"b-{i}" for i in range(25)]
    )
