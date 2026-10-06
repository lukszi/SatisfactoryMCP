"""Every response that names the file it read must say how old that file is.

Field feedback: the client was twice told "nothing is here" because the newest
readable file was an autosave hours behind the live session. The fix is not a new
tool -- it is that ``age_note``, the one line every save-reading tool already prints,
carries the file's mtime (absolute plus relative) and flags an autosave as a file
that may lag the world. These tests pin that sentence at the dataclass level, where
it is written, so every presenter that embeds it inherits the guarantee.
"""

from __future__ import annotations

import time

from satisfactory_mcp.core.text import ago, stamp
from satisfactory_mcp.domain.world.identity import SaveIdentity

# ------------------------------------------------------------------ the units

#: One fixed write moment, so the relative phrases below are computed against an
#: explicit ``now`` rather than against the wall clock mid-test.
_WRITE_NS = 1_700_000_000_000_000_000
_WRITE_S = _WRITE_NS / 1e9


def test_ago_speaks_one_coarse_unit_per_band():
    assert ago(_WRITE_NS, now_s=_WRITE_S + 10) == "under a minute ago"
    assert ago(_WRITE_NS, now_s=_WRITE_S + 90) == "1 min ago"
    assert ago(_WRITE_NS, now_s=_WRITE_S + 59 * 60) == "59 min ago"
    assert ago(_WRITE_NS, now_s=_WRITE_S + 2 * 3600 + 5) == "2h ago"
    assert ago(_WRITE_NS, now_s=_WRITE_S + 47 * 3600) == "47h ago"
    assert ago(_WRITE_NS, now_s=_WRITE_S + 3 * 86400) == "3 days ago"


def test_ago_clamps_a_file_from_the_future_to_fresh():
    """An autosave can land between ``stat`` and ``now``; '-1 min ago' reads as a bug."""
    assert ago(_WRITE_NS, now_s=_WRITE_S - 30) == "under a minute ago"


def test_ago_and_stamp_say_nothing_for_no_mtime():
    assert ago(None) is None
    assert stamp(None) is None
    assert ago(0) is None
    assert stamp(0) is None


def test_stamp_is_local_wall_clock_to_the_minute():
    expect = time.strftime("%Y-%m-%d %H:%M", time.localtime(_WRITE_S))
    assert stamp(_WRITE_NS) == expect


# --------------------------------------------------------------- the sentence


def _header(filename: str, mtime_ns: int | None) -> dict:
    h = {
        "filename": filename,
        "session_name": "Han Solo",
        "play_duration_s": 3600,
        "save_version": 60,
    }
    if mtime_ns is not None:
        h["mtime_ns"] = mtime_ns
    return h


def test_age_note_carries_the_files_own_mtime():
    """Absolute AND relative: the absolute is checkable against the save dialog, the
    relative is the number that answers 'is this the live world'."""
    an_hour_ago = int((time.time() - 3600) * 1e9)
    ident = SaveIdentity(projection={"header": _header("Han Solo_020826-082843.sav", an_hour_ago)})
    note = ident.age_note
    assert f"written {stamp(an_hour_ago)}" in note
    assert "(1h ago)" in note
    assert "manual save" in note


def test_age_note_flags_an_autosave_as_possibly_lagging_the_world():
    an_hour_ago = int((time.time() - 3600) * 1e9)
    ident = SaveIdentity(projection={"header": _header("Han Solo_autosave_0.sav", an_hour_ago)})
    note = ident.age_note
    assert "autosave" in note
    assert "disk may lag the live world" in note


def test_a_manual_save_earns_no_lag_warning():
    """A manual save is a moment the player chose; warning on it would train the
    reader to skim past the warning on the file that deserves it."""
    ident = SaveIdentity(
        projection={"header": _header("Han Solo_020826-082843.sav", int(time.time() * 1e9))}
    )
    assert "disk may lag" not in ident.age_note


def test_age_note_degrades_without_an_mtime_rather_than_inventing_one():
    """A projection cut before ``mtime_ns`` (or a hand-built test header) keeps the
    old sentence, with no 'written None'."""
    ident = SaveIdentity(projection={"header": _header("Han Solo_020826-082843.sav", None)})
    note = ident.age_note
    assert "written" not in note
    assert "None" not in note
    assert "saveVersion 60" in note
