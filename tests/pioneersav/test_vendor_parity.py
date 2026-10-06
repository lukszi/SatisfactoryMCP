"""The agreement with the deleted parser, replayed from a bank of its digests.

``fixtures/vendor_parity.json`` holds what the vendored parser produced for 20 keys of 31 saves
when the two parsers agreed. These tests replay it against the surviving parser, through a
projection filtered back to the schema-11 shape. docs/DEVELOPING.md ("The vendor parity bank")
says why the bank is never re-recorded, what each ``POST_11_ADDITIONS`` entry is, and what an
oracle cannot catch. The replay needs real saves and skips without them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.support.fanout import fanout_width, in_order
from tests.support.paths import FIXTURES, REPO_ROOT

FIXTURE = FIXTURES / "vendor_parity.json"
SIDECAR_MODULE = "satisfactory_mcp.core.saveio.extract"

#: Header keys that describe the FILE rather than the world, so they are excluded from the
#: digest: a save copied to another path or re-read after a touch is the same world.
VOLATILE = {"path", "filename", "mtime_ns", "size"}

#: The game rewrites these three names in place, so one is the banked save only while its
#: header still digests to the banked one; after that it is a later world under an old name.
AUTOSAVE = re.compile(r"_autosave_\d+\.sav$")

#: Everything every schema after 11 added or corrected, named one by one rather than detected,
#: so that a field emitted by mistake is not absorbed with the legitimate additions.
POST_11_ADDITIONS = {
    #: New top-level keys: belt and pipe polylines (12, 13), the splitters and mergers on belt
    #: runs (13), storage (15), power geometry (17) and crates (18). Columns added inside belts
    #: or pipes (14, 15, 20) need no entry because those keys are dropped whole; who is wired
    #: to whom stays compared as ``graph["power"]``.
    "keys": ("belts", "pipes", "attachments", "storage", "power", "crates"),
    #: The banked version label: a projection filtered back to schema 11 claims schema 11.
    "schema_version": 11,
    #: Schema 12. A new field on every record of these keys: top-down placement yaw in degrees.
    "record_fields": {"machines": "yaw", "extractors": "yaw", "generators": "yaw"},
    #: Schema 12. ``structures.instances`` rows were ``[classIndex, x, y, z]`` and gained a
    #: fifth column, the same yaw. A row is positional, so the addition is a length, not a name.
    "row_width": {"structures": 4},
    #: Schema 16, a correction rather than an addition: the three container classes the
    #: schema-11 bucket rule could not see. ``_unfix_16`` undoes it.
    "storage_bucket_fix": (
        "Build_StoragePlayer_C",
        "Build_StorageIntegrated_C",
        "Build_StorageBlueprint_C",
    ),
    #: Schema 19, the second correction: the bucket the crates' contents moved INTO, out of
    #: ``machine`` where the schema-11 rule had filed them. ``_unfix_19`` folds it back.
    "crate_bucket_fix": "crate",
}


def _unfix_16(projection: dict, inventories: dict) -> dict:
    """``inventories`` with schema 16's storage-bucket fix put back, for comparison only.

    The moved containers' contents come off ``storage`` (dropped whole anyway) and back onto
    ``machine``, landing exactly on the old rule's integers, so the rest of this banked key is
    still compared. What that costs is in docs/DEVELOPING.md.
    """
    moved: dict[str, float] = {}
    for row in projection.get("storage") or ():
        if isinstance(row, dict) and row.get("cls") in POST_11_ADDITIONS["storage_bucket_fix"]:
            for item, amount in row.get("items") or ():
                moved[item] = moved.get(item, 0) + amount
    out = {bucket: dict(stacks) for bucket, stacks in inventories.items()}
    for item, amount in moved.items():
        rest = out.get("storage", {}).get(item, 0) - amount
        # Removed rather than left at zero: the old rule never wrote a key it had counted
        # nothing into, so a lingering ``item: 0`` would digest differently and read as drift.
        if rest:
            out["storage"][item] = rest
        else:
            out.get("storage", {}).pop(item, None)
        out["machine"][item] = out.get("machine", {}).get(item, 0) + amount
    return out


def _unfix_19(inventories: dict) -> dict:
    """``inventories`` with schema 19's crate-bucket fix put back, for comparison only.

    A fold: ``crate`` is added back into ``machine`` item for item and the bucket the oracle
    never had is dropped. Nothing is subtracted, so a miscounted crate still moves the digest;
    a crate stack is never zero, so the fold leaves no spurious ``0``.
    """
    out = {bucket: dict(stacks) for bucket, stacks in inventories.items() if bucket != "crate"}
    crate = inventories.get(POST_11_ADDITIONS["crate_bucket_fix"])
    for item, amount in (crate if isinstance(crate, dict) else {}).items():
        machine = out.setdefault("machine", {})
        machine[item] = machine.get(item, 0) + amount
    return out


def as_schema_11(projection: dict) -> dict:
    """The projection with every post-11 addition removed, and two corrections put back.

    Not a general downgrade: it undoes exactly ``POST_11_ADDITIONS`` and leaves every other
    difference -- which is the point, because every other difference is drift.
    """
    out = {k: v for k, v in projection.items() if k not in POST_11_ADDITIONS["keys"]}
    if "schema_version" in out:
        out["schema_version"] = POST_11_ADDITIONS["schema_version"]
    if isinstance(out.get("inventories"), dict):
        # 19 first and 16 second, though the two commute: each touches its own source
        # bucket and both only ever ADD to ``machine``.
        out["inventories"] = _unfix_16(projection, _unfix_19(out["inventories"]))
    for key, field in POST_11_ADDITIONS["record_fields"].items():
        if isinstance(out.get(key), list):
            out[key] = [
                {k: v for k, v in record.items() if k != field}
                if isinstance(record, dict)
                else record
                for record in out[key]
            ]
    for key, width in POST_11_ADDITIONS["row_width"].items():
        payload = out.get(key)
        if isinstance(payload, dict) and isinstance(payload.get("instances"), list):
            out[key] = {
                **payload,
                "instances": [
                    row[:width] if isinstance(row, list) else row for row in payload["instances"]
                ],
            }
    return out


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


@pytest.fixture(scope="module")
def banked() -> dict:
    if not FIXTURE.is_file():
        pytest.skip("vendor parity fixture not committed")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def saves_root() -> Path:
    # `Path("")` is `Path(".")` and IS a directory, so an unset variable must be rejected before
    # the is_dir() check -- otherwise this silently searches the repo root, finds no saves, and
    # reports "none of the banked saves is on this machine" on a machine that has all 31.
    env = os.environ.get("SATISFACTORY_SAVES")
    candidates = [Path(env)] if env else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "FactoryGame" / "Saved" / "SaveGames")
    for root in candidates:
        if root.is_dir():
            return root
    pytest.skip("no save directory on this machine")
    raise AssertionError("unreachable")


def _projection(path: Path) -> dict:
    """One save through the sidecar, as a subprocess, exactly as the server invokes it.

    ``sys.executable`` rather than ``uv run``: the same interpreter, without a ``uv`` process
    and an environment re-sync per save. ``-m`` with this checkout's ``src`` first on
    ``PYTHONPATH``, as ``_child_env`` does.
    """
    inherited = os.environ.get("PYTHONPATH")
    source = str(REPO_ROOT / "src")
    env = {**os.environ, "PYTHONPATH": f"{source}{os.pathsep}{inherited}" if inherited else source}
    out = subprocess.run(
        [sys.executable, "-m", SIDECAR_MODULE, str(path)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        check=False,  # a refusal is data here: the caller asserts on the payload, not the code
    )
    return json.loads(out.stdout.decode("utf-8", "replace"))


def test_the_banked_reference_is_what_it_claims(banked):
    """The fixture is evidence, so its own shape is worth pinning.

    A truncated or half-written bank would make every comparison below vacuously pass. The
    schema stays 11 for ever: it records what the oracle emitted, not what this parser emits.
    """
    assert banked["_meta"]["saves"] == len(banked["saves"]) == 31
    assert banked["_meta"]["schema_version"] == 11
    for name, entry in banked["saves"].items():
        assert "header" in entry, name
        assert entry["n_objects_value"] > 0, name
        # 20 projection keys plus the readable count beside them.
        assert len(entry) == 21, (name, len(entry))


def test_the_schema_11_filter_removes_the_new_fields_and_only_those():
    """The mechanism the comparison below now depends on, pinned without a save.

    Two halves, and the second is the one that matters. A filter that removed too much -- or
    that simply returned a constant -- would make every digest agree for ever, so it is not
    enough to show that adding the later schemas' fields leaves the digests alone: changing a
    schema-11 field must still move them.
    """
    eleven = {
        "schema_version": 11,
        "machines": [{"cls": "Build_SmelterMk1_C", "pos": [1.0, 2.0, 3.0]}],
        "extractors": [{"cls": "Build_MinerMk2_C", "pos": [4.0, 5.0, 6.0]}],
        "generators": [{"cls": "Build_GeneratorCoal_C", "pos": [7.0, 8.0, 9.0]}],
        "structures": {"classes": ["Build_Foundation_8x1_01_C"], "instances": [[0, 10, 20, 30]]},
        # The bucketing the deleted parser was compared against: a Personal Storage Box's
        # contents counted as a machine buffer, and 60 of the 100 Iron Plate -- the box's
        # share -- missing from what the player can spend. Wrong, and what the bank holds.
        "inventories": {
            "player": {"Desc_Wire_C": 7},
            "storage": {"Desc_IronPlate_C": 40},
            "machine": {"Desc_IronPlate_C": 60, "Desc_Rubber_C": 5},
        },
        "warnings": [],
    }
    latest = {
        "schema_version": 20,
        "machines": [{"cls": "Build_SmelterMk1_C", "pos": [1.0, 2.0, 3.0], "yaw": -20.0}],
        "extractors": [{"cls": "Build_MinerMk2_C", "pos": [4.0, 5.0, 6.0], "yaw": 90.0}],
        "generators": [{"cls": "Build_GeneratorCoal_C", "pos": [7.0, 8.0, 9.0], "yaw": 0.0}],
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, 10, 20, 30, -20.0]],
        },
        # A curved belt, so the schema-15 tangent column is actually present and not just
        # declared absent: a filter that only ever saw the short row would pass this test
        # while dropping nothing. Both rows carry schema 20's actor index at column 3.
        "belts": {
            "classes": ["Build_ConveyorBeltMk3_C"],
            "segments": [
                [0, 0, [[1, 2, 3]], 6],
                [0, 0, [[1, 2, 3], [4, 5, 6]], 7, [[7, 8, 9, 1, 2, 3]]],
            ],
        },
        "attachments": [
            {"cls": "Build_ConveyorAttachmentSplitter_C", "pos": [1.0, 2.0, 3.0], "yaw": 90.0}
        ],
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 3, "fluid": "Desc_Water_C"}],
            "segments": [[0, 0, [[1, 2, 3], [4, 5, 6]], 4, [[7, 8, 9, 1, 2, 3]]]],
        },
        # Schema 17. A pole and a wire, so the key being dropped whole is actually populated:
        # a filter tested against an empty ``power`` would pass while dropping nothing.
        "power": {
            "poles": {"classes": ["Build_PowerPoleMk1_C"], "instances": [[0, 1, 2, 3, 0.0, 0]]},
            "wires": [[1, 2, 703, 40, 50, 703]],
        },
        # Two containers, and the second is the one schema 16 moved. Its 60 Iron Plate are in
        # ``storage`` below and were in ``machine`` before, which is exactly what _unfix_16
        # has to undo -- and the Rubber beside them is a real machine buffer that must not be
        # touched by the undoing.
        "storage": [
            {
                "cls": "Build_StorageContainerMk1_C",
                "instance": "x.Build_StorageContainerMk1_C_1",
                "pos": [1.0, 2.0, 3.0],
                "yaw": 90.0,
                "items": [["Desc_IronPlate_C", 40]],
                "slots": 24,
            },
            {
                "cls": "Build_StoragePlayer_C",
                "instance": "x.Build_StoragePlayer_C_2",
                "pos": [4.0, 5.0, 6.0],
                "yaw": None,
                "items": [["Desc_IronPlate_C", 60]],
                "slots": 10,
            },
        ],
        # Schema 18's key, populated for the reason ``power`` above is -- and the Rubber in
        # it is DELIBERATELY the same 5 units the CRATE bucket below holds: schema 19 moved
        # a crate's contents out of ``machine`` into that bucket, so the filter has to fold
        # them back to land on the schema-11 shape, where they were a "machine buffer". A
        # filter that only dropped the ``crate`` key would leave the eleven bucket 5 short.
        "crates": [
            {
                "cls": "BP_Crate_C",
                "instance": "x.BP_Crate_C_3",
                "pos": [7.0, 8.0, 9.0],
                "yaw": -90.0,
                "kind": "death",
                "items": [["Desc_Rubber_C", 5]],
                "slots": 1,
            }
        ],
        "inventories": {
            "player": {"Desc_Wire_C": 7},
            "storage": {"Desc_IronPlate_C": 100},
            "machine": {},
            "crate": {"Desc_Rubber_C": 5},
        },
        "warnings": [],
    }
    filtered = as_schema_11(latest)
    assert filtered == eleven, "the filter did not land back on the schema-11 shape"
    assert {k: _digest(v) for k, v in filtered.items()} == {
        k: _digest(v) for k, v in eleven.items()
    }

    moved = dict(latest)
    moved["machines"] = [{**latest["machines"][0], "pos": [1.0, 2.0, 99.0]}]
    assert _digest(as_schema_11(moved)["machines"]) != _digest(eleven["machines"]), (
        "the filter hides a changed schema-11 field, which is the drift the bank exists to catch"
    )

    # And the same demand of the schema-16 undo specifically, because it is a step that
    # RESTORES a value rather than dropping one: a reconstruction that simply copied the
    # bank's shape would pass the equality above and hide every stack in the key for ever. A
    # container the two parsers would have read differently still has to move the digest.
    misread = dict(latest)
    misread["storage"] = [
        {**latest["storage"][0], "items": [["Desc_IronPlate_C", 41]]},
        latest["storage"][1],
    ]
    misread["inventories"] = {
        **latest["inventories"],
        "storage": {"Desc_IronPlate_C": 101},
    }
    assert _digest(as_schema_11(misread)["inventories"]) != _digest(eleven["inventories"]), (
        "a miscounted container reads as agreement, which makes the whole key vacuous"
    )

    # And of the schema-19 undo, which restores a value the same way: a crate this parser
    # started miscounting has to move the folded ``machine`` digest, or the stacks that
    # moved buckets would have left the banked comparison rather than been reconstructed
    # into it.
    miscrated = dict(latest)
    miscrated["crates"] = [{**latest["crates"][0], "items": [["Desc_Rubber_C", 6]]}]
    miscrated["inventories"] = {
        **latest["inventories"],
        "crate": {"Desc_Rubber_C": 6},
    }
    assert _digest(as_schema_11(miscrated)["inventories"]) != _digest(eleven["inventories"]), (
        "a miscounted crate reads as agreement, which retires the moved stacks from the bank"
    )


def test_a_new_top_level_key_cannot_escape_the_comparison(banked, projection):
    """``POST_11_ADDITIONS["keys"]`` is exactly what the projection has that the bank has not.

    A hand-maintained list fails by omission. Held against the committed fixture, so a new key
    with no entry fails on the machine of whoever added it; and both ways, so a stale entry
    fails here rather than as a missing key in the whole-folder replay.
    """
    banked_keys = {key for entry in banked["saves"].values() for key in entry}
    # The bank names the object count twice -- ``n_objects`` for the digest and
    # ``n_objects_value`` for the integer beside it -- and only the first is a projection key.
    banked_keys.discard("n_objects_value")

    assert set(projection) - banked_keys == set(POST_11_ADDITIONS["keys"]), (
        "a top-level projection key is outside the banked comparison without an entry in "
        "POST_11_ADDITIONS -- decide whether it is a legitimate addition the deleted oracle "
        "never saw, or a key this parser started emitting by mistake"
    )
    assert banked_keys <= set(projection), (
        "the bank holds a key this projection no longer emits -- the agreement cannot be "
        f"replayed for {sorted(banked_keys - set(projection))}"
    )


@pytest.mark.integration
@pytest.mark.whole_folder
def test_this_parser_still_produces_what_the_two_agreed_on(banked, saves_root):
    """The replayed acceptance test: every banked key of every save still digests the same.

    The sidecar runs go out on a thread pool (each is its own subprocess); the comparison runs
    in the bank's order through ``in_order``, so the drift report names what a serial loop would.
    """
    by_name = {p.name: p for p in saves_root.rglob("*.sav")}
    present = [
        (name, entry, by_name[name]) for name, entry in banked["saves"].items() if name in by_name
    ]
    if not present:
        pytest.skip("none of the banked saves is on this machine")

    checked = 0
    drift: list[tuple[str, str]] = []
    width = fanout_width()
    with ThreadPoolExecutor(max_workers=width) as pool:
        for (name, entry, _path), proj in in_order(
            pool, present, lambda item: _projection(item[2]), width=width
        ):
            assert "error" not in proj, (name, proj.get("detail"))
            assert proj["schema_version"] == 22, (name, "unexpected schema for the filter")
            header = {k: v for k, v in proj["header"].items() if k not in VOLATILE}
            if AUTOSAVE.search(name) and _digest(header) != entry["header"]:
                continue
            proj = as_schema_11(proj)
            for key, want in entry.items():
                if key == "n_objects_value":
                    assert proj["n_objects"] == want, (name, key)
                    continue
                value = (
                    {k: v for k, v in proj["header"].items() if k not in VOLATILE}
                    if key == "header"
                    else proj[key]
                )
                if _digest(value) != want:
                    drift.append((name, key))
            checked += 1
    assert checked, "the banked saves are on this machine but none was compared"
    assert not drift, f"drifted from the banked agreement on {len(drift)} key(s): {drift[:8]}"
