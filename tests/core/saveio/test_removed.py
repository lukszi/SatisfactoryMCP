"""The projection's ``removed`` key: 889 collected actors from either parser, byte for byte.

``extract._removed`` is the one projection field that has to read two completely
different parser outputs and produce the SAME bytes. ``pioneersav`` merges the format's three
destroyed-actor lists into ``destroyed_actors``; the vendored parser exposes them separately,
as each level's ``collectables1``/``collectables2`` plus two save-level lists. Both are
walked here through the public function, with hand-built stand-ins for each parser's shape,
because the interesting failures are all in the reconciliation rather than in either parser:

* **order.** Which of the three lists a parser walks first is an artefact. Unsorted, the two
  engines emit the same 889 actors in different orders, the projection stops being
  byte-comparable, and every parity diff turns into a false positive.
* **``str()`` on a reference.** The vendored ``ObjectReference.__str__`` renders the whole
  object -- ``<ObjectReference: levelName=..., pathName=...>`` -- so a leaf taken from it ends
  in ``>`` and every one of the 889 comes out unique. That produced 889 distinct classes where
  there are 270, and it is pinned below with a stand-in that has the same ``__str__``.
* **the class.** These lists carry no class path at all, only an instance name, and the game
  builds those three different ways. ``_removed_class`` recovers a class by stripping from the
  right, and is approximate on purpose -- see its docstring, and the last test here.

No fixture and no save: every input below is a name shape taken from the reference save's
889 actors, written out literally so the expected output can be read next to it.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.core.saveio.extract import _removed, _removed_class

#: Four real instance names from the reference save, one per shape the game writes.
#: ``BP_Crystal2_228`` is the shape that makes the recovery approximate: an instance number
#: glued straight onto the blueprint name with no separator.
REAL_NAMES = {
    "BP_Crystal_mk3_C_10": "BP_Crystal_mk3",
    "BP_Crystal2_228": "BP_Crystal2",
    "BP_MercerShrine_C_UAID_40B076DF2F7983B301_1397405905": "BP_MercerShrine",
    "FGItemPickup_Spawnable_UAID_04421A9713F0395B01_1619382940": "FGItemPickup_Spawnable",
}

CELL_A = "03ZCPBHIVK00B2FFLHN6JMVAG"
CELL_B = "5XHD5LD7LFXCYHGVP956D1C6E"
PREFIX = "Persistent_Level:PersistentLevel."


class FakeRef:
    """A stand-in for the vendored parser's ObjectReference, ``__str__`` included.

    The ``__str__`` is the point. It is reproduced here so that a future ``str(ref)`` creeping
    back into ``_removed`` fails a test instead of quietly making every leaf unique.
    """

    def __init__(self, level: str, path: str) -> None:
        self.levelName = level
        self.pathName = path

    def __str__(self) -> str:  # pragma: no cover - only reached if _removed regresses
        return f"<ObjectReference: levelName={self.levelName}, pathName={self.pathName}>"


class OwnParser:
    """What ``pioneersav`` hands over: the three lists already merged and deduplicated."""

    def __init__(self, refs) -> None:
        self.destroyed_actors = list(refs)


class VendoredParser:
    """What the vendored parser hands over: three lists, none of them merged.

    Deliberately has NO ``destroyed_actors`` attribute, because that absence is exactly how
    ``_removed`` decides which shape it is looking at.
    """

    class _Level:
        def __init__(self, one, two) -> None:
            self.collectables1 = [FakeRef(*r) for r in one]
            self.collectables2 = [FakeRef(*r) for r in two]

    def __init__(self, levels, drop_pods=(), extra=()) -> None:
        self.levels = [self._Level(one, two) for one, two in levels]
        self.dropPodObjectReferenceList = [FakeRef(*r) for r in drop_pods]
        self.extraObjectReferenceList = [FakeRef(*r) for r in extra]


def _refs(*pairs):
    return [(cell, PREFIX + leaf) for cell, leaf in pairs]


# ------------------------------------------------------------------- the reduction


def test_the_actor_path_is_reduced_to_its_leaf():
    """``Persistent_Level:PersistentLevel.`` opens all 889 paths and says nothing.

    Keeping it would triple the size of this key for no information. The leaf is taken after
    the last dot, which is also what makes the two parsers comparable -- one of them spells
    the prefix slightly differently on some levels.
    """
    out = _removed(OwnParser(_refs((CELL_A, "BP_Shroom_12"))))
    assert out["instances"] == [[0, "BP_Shroom_12"]]
    assert out["cells"] == [CELL_A]


def test_a_reference_with_no_path_is_dropped_rather_than_counted():
    """An empty pathName has no leaf, so there is no actor to report and nothing to classify.
    Counting it would put a phantom entry under the class ``""``."""
    out = _removed(
        OwnParser(
            [
                (CELL_A, ""),
                (CELL_A, "Persistent_Level:PersistentLevel."),
                (CELL_A, PREFIX + "BP_WAT60"),
            ]
        )
    )
    assert out["instances"] == [[0, "BP_WAT60"]]
    assert out["counts"] == {"BP_WAT60": 1}


def test_cells_are_interned_because_284_of_them_carry_889_actors():
    """A 25-character partition-cell id repeated 889 times is 22 kB of nothing.

    The index is positional into ``cells``, assigned in the order cells are first seen -- which
    after the sort is alphabetical, so the table is stable between runs and between parsers.
    """
    out = _removed(
        OwnParser(
            _refs(
                (CELL_A, "BP_Shroom_1"),
                (CELL_B, "BP_WAT1_C_7"),
                (CELL_A, "BP_Shroom_2"),
            )
        )
    )
    assert out["cells"] == sorted([CELL_A, CELL_B])
    assert [ix for ix, _leaf in out["instances"]] == [0, 0, 1]
    assert [out["cells"][ix] for ix, _leaf in out["instances"]] == [CELL_A, CELL_A, CELL_B]


# --------------------------------------------------------------------- the sort


def test_the_sort_is_what_makes_the_two_engines_emit_identical_bytes():
    """Same actors, three different input orders, one output.

    The order a parser produces is decided by which of the three lists it walks first, and
    neither order is more correct. Sorting by ``(cell, path)`` is what lets the projection be
    compared byte for byte between the engines -- which is how ``removed`` was verified at all
    -- and what lets it be used as a cache key.
    """
    pairs = _refs(
        (CELL_B, "BP_WAT1_C_7"),
        (CELL_A, "BP_Shroom_2"),
        (CELL_A, "BP_Shroom_1"),
    )
    first = _removed(OwnParser(pairs))
    for order in (list(reversed(pairs)), sorted(pairs, key=lambda p: p[1])):
        assert _removed(OwnParser(order)) == first
    assert [leaf for _ix, leaf in first["instances"]] == [
        "BP_Shroom_1",
        "BP_Shroom_2",
        "BP_WAT1_C_7",
    ]


def test_both_parser_shapes_produce_the_same_json():
    """The reconciliation, in miniature: 3 actors, spread over the vendored parser's four
    lists with one of them written twice, against the same 3 already merged. The two must
    serialise to the same bytes -- which is the measurement the 20-of-20-keys parity run makes
    on the whole save, reduced to something that needs no .sav to check."""
    merged = _refs(
        (CELL_A, "BP_MercerShrine_C_UAID_40B076DF2F7983B301_1397405905"),
        (CELL_A, "BP_DropPod3_1"),
        (CELL_B, "BP_Crystal2_228"),
    )
    vendored = VendoredParser(
        levels=[
            # collectables1 and collectables2 overlap, as they do on 854 of 889 real actors.
            (
                _refs((CELL_B, "BP_Crystal2_228")),
                _refs((CELL_B, "BP_Crystal2_228"), (CELL_A, "BP_DropPod3_1")),
            ),
        ],
        drop_pods=_refs((CELL_A, "BP_DropPod3_1")),
        extra=_refs((CELL_A, "BP_MercerShrine_C_UAID_40B076DF2F7983B301_1397405905")),
    )

    def dumps(payload) -> str:
        return json.dumps(payload, separators=(",", ":"))

    assert dumps(_removed(VendoredParser(levels=[]))) == dumps(
        {"cells": [], "instances": [], "counts": {}}
    )
    assert dumps(_removed(vendored)) == dumps(_removed(OwnParser(merged)))


def test_the_vendored_lists_are_deduplicated_before_they_are_counted():
    """It hands over three lists that overlap, so the same actor arrives more than once.

    Counting the duplicates would report 1,755 collected actors where 889 exist -- an
    overstatement that looks entirely plausible, which is why it is pinned rather than left to
    the parity diff to notice.
    """
    twice = _refs((CELL_A, "BP_Shroom_1"))
    out = _removed(VendoredParser(levels=[(twice, twice)], drop_pods=twice, extra=twice))
    assert out["instances"] == [[0, "BP_Shroom_1"]]
    assert out["counts"] == {"BP_Shroom": 1}


def test_the_leaf_never_comes_from_str_on_a_reference():
    """The bug that produced 889 distinct classes instead of 270.

    ``str()`` on the vendored ObjectReference renders the whole object, so the leaf ended in
    ``>`` and carried the instance number with it -- every actor unique, every class count 1,
    and the census silently useless. ``FakeRef.__str__`` reproduces that rendering, so a leaf
    containing ``<`` or ``>`` here means the regression is back.
    """
    out = _removed(VendoredParser(levels=[(_refs((CELL_A, "BP_Shroom_1")), [])]))
    leaves = [leaf for _ix, leaf in out["instances"]]
    assert leaves == ["BP_Shroom_1"]
    assert not any(c in leaf for leaf in leaves for c in "<>=")
    assert list(out["counts"]) == ["BP_Shroom"]


# ----------------------------------------------------------------- the class


@pytest.mark.parametrize(("name", "expected"), sorted(REAL_NAMES.items()))
def test_the_class_is_recovered_from_each_real_name_shape(name, expected):
    """Three name shapes, all in the reference save, and the stripping order that handles all
    three: the trailing index first, then a ``_UAID_<hex>`` world id if present, then a
    trailing ``_C``. Any other order fails one of them -- taking ``_C`` off first leaves the
    UAID's index behind on a shrine, and taking the UAID off first finds nothing on a slug."""
    assert _removed_class(name) == expected


def test_the_recovery_is_approximate_and_says_so_by_example():
    """``BP_Crystal2_228`` cannot be told from a class literally named ``BP_Crystal2``.

    68% of these names are level-placed actors whose number is glued straight onto the
    blueprint name, so the census groups by what the name SHOWS rather than by a class list
    nobody has. That is why callers match a prefix: ``BP_Crystal_mk21_23`` still shows its
    ``mk2``, so slug tiers survive the gluing even though the exact class does not.
    """
    assert _removed_class("BP_Crystal2_228") == "BP_Crystal2"
    assert _removed_class("BP_Crystal_mk21_23") == "BP_Crystal_mk21"
    assert _removed_class("BP_Crystal_mk21_23").startswith("BP_Crystal_mk2")
    # And a name that is nothing but the stripped parts falls back to itself rather than "".
    assert _removed_class("_C") == "_C"
    assert _removed_class("42") == "42"


def test_counts_are_sorted_so_the_census_is_stable():
    """Insertion order here is the sorted actor order, which is not alphabetical by class.
    Sorting the counts is what keeps the projection's bytes fixed for a fixed save."""
    out = _removed(
        OwnParser(
            _refs(
                (CELL_A, "BP_WAT1_C_7"),
                (CELL_A, "BP_Crystal2_228"),
                (CELL_A, "BP_Shroom_1"),
            )
        )
    )
    assert list(out["counts"]) == sorted(out["counts"])
