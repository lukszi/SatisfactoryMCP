"""The agreement with the deleted parser, kept falsifiable after it was deleted.

For the length of this reimplementation the acceptance test was a diff: run the same save through
the vendored GPL-3.0 parser and through ours, and compare the projection JSON leaf for leaf. That
diff was the whole argument, and **deleting the library destroyed the ability to re-run it.**

So it was banked first. ``fixtures/vendor_parity.json`` holds, per save and per projection key, a
digest of the value the *vendored* parser produced, recorded in the last minutes before its
deletion, when the two agreed on **all 20 keys on all 31 saves it could read**. This file replays
that comparison against the surviving parser.

**What this can and cannot catch.** It catches our parser drifting away from what the two agreed
on -- which is the regression that matters, because every claim in ``docs/savparse-notes.md``
rests on that agreement. It cannot catch a fault they *shared*: if both parsers misread the same
field the same way, the digests agree and always will. That limit is inherent in an oracle, was
inherent while the oracle was still here, and is the reason the notes also record predicates
measured against the bytes rather than against the other parser.

These tests need real saves and skip without them. That is deliberate: the rest of the suite runs
on committed fixtures with no game install, and this one is the exception, because a digest of a
projection is only meaningful against the save it came from.

**Why the bank is not re-banked, ever.** The projection has since grown a placement yaw and a
``belts`` key (schema 12), a ``pipes`` key (schema 13), a ``storage`` key (schema 15), a
``power`` key (schema 17) and a ``crates`` key (schema 18). None
of those fields existed while
the oracle did, so it never had an opinion about them, and re-recording the bank against this
parser would replace an independent measurement with this parser's own output -- the one thing
that would make every test here vacuous. So the comparison runs on a projection FILTERED BACK
to the schema-11 shape, by the explicit list in ``POST_11_ADDITIONS`` below. Additive fields are
legitimately outside the deleted oracle's scope; a *changed* schema-11 field is exactly what
this still catches.

Every later schema adds its own entry to that list rather than re-banking, which is why the
list is keyed by what was added and annotated with which schema added it.

**And the list is pinned in both directions**, because a hand-maintained list of exceptions
fails by omission rather than by error. Until ``test_a_new_top_level_key_cannot_escape_the
_comparison`` a schema-18 key added to the projection and forgotten here would simply not be
compared -- silently, on every save, for ever, with no test failing to say so. That test
reads the committed fixture and demands that what it holds beyond the bank is EXACTLY
``POST_11_ADDITIONS["keys"]``: a new key with no entry fails, and a stale entry for a key
that no longer exists fails too.

**Schema 16 is the first entry that is not an addition**, and it needed a decision rather than
a line: it CORRECTED ``inventories``, which is one of the banked keys. The choice made, and
the choice rejected, are argued in ``_unfix_16``. **Schema 19 is the second**, and it corrects
the same key: a crate's contents moved out of ``inventories["machine"]`` into their own
``crate`` bucket, and ``_unfix_19`` folds them back for the comparison.
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
from _pool import fanout_width, in_order

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "vendor_parity.json"
SIDECAR_MODULE = "satisfactory_mcp.core.saveio.extract"

#: Header keys that describe the FILE rather than the world, so they are excluded from the
#: digest: a save copied to another path or re-read after a touch is the same world.
VOLATILE = {"path", "filename", "mtime_ns", "size"}

#: The game rewrites these three names in place, so one is the banked save only while its
#: header still digests to the banked one; after that it is a later world under an old name.
AUTOSAVE = re.compile(r"_autosave_\d+\.sav$")

#: Everything every schema after 11 added, named one by one rather than detected. Guessing
#: structurally -- "drop keys the bank has never seen", "drop record fields the bank cannot know
#: about" -- would also silently absorb a field this parser started emitting BY MISTAKE, which is
#: the class of regression the bank exists to catch. Written out, adding to this list is a
#: decision somebody has to make and a reviewer can see.
POST_11_ADDITIONS = {
    #: Whole new top-level keys: per-belt spline polylines (12), per-pipe ones (13), the
    #: splitters and mergers those belt runs pass through (13), and the containers and fluid
    #: buffers with their contents (15).
    #:
    #: Schema 14 added a FOURTH COLUMN to a pipe segment -- the index of its own actor in
    #: ``graph["actors"]``, which joins a drawn pipe to the connection graph -- and needs no
    #: entry of its own for one reason worth writing down rather than leaving to be rederived:
    #: the change is confined inside ``pipes``, and ``pipes`` is already dropped whole. Had it
    #: widened a schema-11 row instead, it would have owed ``row_width`` an entry, exactly as
    #: ``structures`` does below.
    #:
    #: **Schema 15's spline tangents are the same case, and it is worth saying so out loud
    #: because they land in two keys rather than one.** A belt segment gained a fourth column
    #: and a pipe segment a fifth -- the curve through the points either side of each span --
    #: and both are inside ``belts`` and ``pipes``, which are dropped whole here. So the
    #: tangents need no entry, and the reason is not "they are new" (everything in this list is
    #: new) but "the key that carries them was already outside the oracle's scope". The
    #: ``storage`` key beside them is a genuinely new top-level name and IS listed.
    #:
    #: **Schema 17's ``power`` is a new top-level name and is listed, and the interesting part
    #: is what is NOT listed beside it.** That key carries the poles and the drawn span of
    #: every wire -- but not who is wired to whom, which has been ``graph["power"]`` since
    #: schema 11 and is one of the twenty keys the deleted oracle was banked on. So ``graph``
    #: stays inside the comparison, unfiltered, and a schema-17 sidecar that changed the ORDER
    #: of the power edges (which is what ``wires`` is positionally joined to) or dropped one
    #: would move that digest on all 31 saves and fail here. That is the intended reading: the
    #: geometry is new and outside the oracle's scope, the connectivity is not new and is
    #: still being checked against it.
    #:
    #: **Schema 18's ``crates`` is a new top-level name and is listed, and what it did NOT
    #: touch was the point at the time.** A crate's contents had been inside
    #: ``inventories["machine"]`` since schema 11 -- the bucket rule filed a component
    #: called ``Inventory`` on a non-storage, non-player owner with the smelter buffers --
    #: and schema 18 deliberately left them there, so 18 cost one line where 16 cost a
    #: function. Schema 19 then made the move 18 declined: see ``crate_bucket_fix`` below
    #: and ``_unfix_19``, which is the function 19 owed all along.
    #:
    #: **Schema 20 gave a belt segment the actor column 14 gave a pipe, and it is 14's case
    #: for 14's reason** -- confined inside ``belts``, which is dropped whole, so no entry.
    #: It gets a paragraph anyway because 20 does not only APPEND: the actor goes at column 3
    #: to match the pipe layout exactly, which MOVES schema 15's tangents from column 3 to
    #: column 4 within the row. Still nothing to undo here, because the whole key leaves the
    #: comparison -- but had that row been a banked one, moving a column would have owed a
    #: reconstruction rather than a width, which is a heavier debt than anything in this list.
    "keys": ("belts", "pipes", "attachments", "storage", "power", "crates"),
    #: The version label is itself one of the 20 banked keys, and it is the one key that is
    #: SUPPOSED to differ. A projection filtered back to the schema-11 shape claims the
    #: schema-11 number; leaving the current number here would report drift on every save on
    #: the grounds that the schema changed, which is the thing being announced rather than a
    #: fault.
    "schema_version": 11,
    #: Schema 12. A new field on every record of these keys: top-down placement yaw in degrees.
    "record_fields": {"machines": "yaw", "extractors": "yaw", "generators": "yaw"},
    #: Schema 12. ``structures.instances`` rows were ``[classIndex, x, y, z]`` and gained a
    #: fifth column, the same yaw. A row is positional, so the addition is a length, not a name.
    "row_width": {"structures": 4},
    #: Schema 16, and the first entry here that is not an ADDITION. See ``_unfix_16`` below for
    #: what it undoes and why it is undone this way; these are the three container classes the
    #: schema-11 bucket rule could not see, which is the whole of the difference.
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

    **The one place this file compares a CHANGED schema-11 key rather than dropping a new
    one, so the choice is argued rather than made.** Schema 16 corrected
    ``inventories["storage"]``: the bucket rule matched three substrings where it meant
    membership of STORAGE_CLASSES, so the Personal Storage Boxes, the HUB's own container and
    the Blueprint Designer's were bucketed as machine buffers -- 10,667 units over 31 item
    classes on the reference save, moved out of ``machine`` and into ``storage``. ``header``
    aside, ``inventories`` is one of the twenty keys the vendored parser and this one were
    shown to agree on, and that bank is never re-recorded. So the fix moves a banked value on
    every save, and something has to give.

    The alternative was a per-field exception: declare ``inventories`` outside the oracle's
    scope from schema 16 on, and stop comparing it. That is one line and it is what the phrase
    "documented exception" would have bought -- at the price of retiring the key whole. The
    two parsers agreed about the PLAYER bucket, about the machine bucket's other 6,500 stacks
    and about all 52 item classes; none of that is affected by this fix, and none of it would
    be checked again.

    So the split is reconstructed instead. Only three classes moved, ``storage`` carries those
    same containers' contents per instance, and it is dropped whole here anyway -- so
    subtracting them from ``storage`` and adding them back to ``machine`` lands exactly on the
    numbers the old rule produced, in integers, with nothing rounded. What the bank goes on
    checking is everything else in the key.

    **What this does cost, stated rather than left to be found.** The reconstruction is
    computed from this parser's own ``storage`` rows, so if this parser started misreading one
    of those eight containers, ``storage`` and ``inventories`` would move together and cancel:
    that one drift is now invisible here. It is real, it is confined to eight containers of
    one key, and it is smaller than the exception's cost, which is the whole key on every
    save for ever. It is also not a new KIND of blindness -- an oracle can never catch a
    fault the two sides share, which the module docstring says at the top.
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

    The second correction of a banked key, and a cheaper reconstruction than ``_unfix_16``'s
    because the fix was cheaper: 16 changed which bucket a rule ROUTED eight containers to,
    so the undo has to recompute the routing from the ``storage`` rows; 19 moved the crates'
    stacks out of ``machine`` into a NEW bucket of their own, so the undo is a fold -- add
    ``crate`` back into ``machine``, item for item in integers, and drop the key the oracle
    never had. The old rule never wrote a ``machine`` entry it had counted nothing into, and
    a crate stack is by construction non-zero, so the fold cannot leave a spurious ``0``.

    What this deliberately does NOT do is cancel a drift. The folded total is exactly what
    the schema-11 rule produced only while this parser reads each crate's component the way
    it always has -- a crate the parser started miscounting moves the folded ``machine``
    digest on every save that holds one, which keeps the banked key falsifiable for the
    stacks that moved as well as the ones that stayed. (The blindness ``_unfix_16`` states
    -- a misread that moves two reconstructed values together and cancels -- has no analogue
    here, because nothing is subtracted.)
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

    ``sys.executable``, not ``uv run python``. The interpreter is identical -- pytest is
    already running inside the environment ``uv run`` would have selected -- so this drops a
    ``uv`` process per save for nothing given up, which over 31 saves was measurable. It also
    removes two ways for this test to mean something other than what it says: ``uv run``
    re-resolves the environment unless ``UV_NO_SYNC`` is set, so a bare ``pytest`` invocation
    used to fire 31 syncs, and it needs ``uv`` on ``PATH``, which the thing under test does
    not. ``-m`` with this checkout's ``src`` first on ``PYTHONPATH``, as ``_child_env`` does.
    """
    inherited = os.environ.get("PYTHONPATH")
    source = str(REPO / "src")
    env = {**os.environ, "PYTHONPATH": f"{source}{os.pathsep}{inherited}" if inherited else source}
    out = subprocess.run(
        [sys.executable, "-m", SIDECAR_MODULE, str(path)],
        cwd=str(REPO),
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

    **The list of exceptions is hand-maintained, and a hand-maintained list fails by
    omission.** Everything above pins what the filter DOES; nothing pinned what it was given
    to do. So a schema-19 key added to ``extract`` and not added here would be compared
    against a bank that has never heard of it -- ``proj[key]`` would raise on the first save
    and the fix would look like adding a line to this list, which is exactly the reflex that
    would have retired the key from the comparison for ever, silently, with no reviewer
    seeing a decision being made.

    Held against the committed fixture rather than against a live parse, so it runs on a
    clone with no game install and no ``.sav`` -- which is the whole point: this has to fail
    for the person who added the key, on their machine, in the same run that added it.

    Both directions, and the second is not decoration. A key DROPPED from the projection
    while its entry stays here would leave the filter removing something that is not there,
    and the integration test below would report it as a missing key rather than as a stale
    exception -- 31 saves late, and only on a machine that has them.
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
    """The replayed acceptance test, and the reason the bank exists.

    Every key of every save that both parsers once read must still digest to the value the
    vendored one produced. A difference here is this parser having drifted from the only
    independent check it ever had.

    Compared through ``as_schema_11``: what the oracle never saw cannot be part of an
    agreement with it.

    **The 31 sidecar runs go out in parallel and the comparison stays serial**, which is the
    split that matters. Each save is its own subprocess and always was, so nothing here
    shares state and the pool is a thread pool: the threads only wait on children, and
    Windows process-spawn cost is a thing to avoid paying twice. What is deliberately NOT
    parallel is the loop below -- it runs over ``in_order``, in the bank's own order, so
    ``drift`` accumulates the same pairs in the same sequence and the ``assert`` that names
    the first eight names the same eight it named when this took 88 s. A pool that reported
    the first failure it happened to see would have turned "``Han solo`` drifted on
    ``inventories``" into whichever save lost the race.
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
