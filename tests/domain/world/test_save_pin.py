"""``as_of=``: a pinned answer is consistent, or it refuses out loud.

THE HAZARD, which ``test_the_hazard`` below reproduces: a client makes several tool calls,
the game autosaves in the middle, call 1 reads save A and call 3 reads save B, and the answer
composed from both never existed in either world. Nothing made that detectable, because a
response names its FILENAME and an autosave rewrites its own file under the same name.

No game install and no ``.sav`` needed anywhere in this file: the loader is stubbed and every
header is synthetic, because what is under test is identity and refusal rather than any
reading of a world.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio import projection as proj
from satisfactory_mcp.domain.world import pin
from satisfactory_mcp.domain.world import timeline as tl
from satisfactory_mcp.domain.world.identity import TOKEN_SHAPE, SaveIdentity, save_token
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp import app as mcp_app
from satisfactory_mcp.interfaces.mcp.tools import world as world_tools

#: One world, and two of its autosaves. B is 18 minutes of play and 5 minutes of wall clock
#: after A, and -- this is the whole point -- they are the same FILE: the game recycles
#: ``autosave_0``, so B was written over A's bytes and A no longer exists on disk.
WORLD = "X2faPVKjX06VaRzClNv5KQ"
_WRITTEN_NS = 1_770_000_000_000_000_000


def _header(**over) -> dict:
    h = {
        "filename": "Han Solo_autosave_0.sav",
        "session_name": "Han Solo",
        "save_identifier": WORLD,
        "play_duration_s": 435_840,  # 121h04m
        "mtime_ns": _WRITTEN_NS,
        "size": 2_900_000,
        "save_version": 60,
    }
    h.update(over)
    return h


HEADER_A = _header()
HEADER_B = _header(play_duration_s=436_920, mtime_ns=_WRITTEN_NS + 300 * 10**9, size=2_901_100)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """The token ledger, in a directory of this test's own."""
    monkeypatch.setattr(pin.config, "cache_dir", lambda: tmp_path)
    return tmp_path


# ------------------------------------------------------------------- the token


def test_a_token_is_a_prefix_and_twelve_hex_digits():
    token = save_token(HEADER_A)
    assert TOKEN_SHAPE.fullmatch(token), token
    assert len(token) == len("sav:") + 12


def test_the_same_world_state_always_gets_the_same_token():
    assert save_token(HEADER_A) == save_token(dict(HEADER_A))


def test_two_autosaves_sharing_a_filename_get_different_tokens():
    """THE reason the filename is not the identity. Both of these are ``autosave_0``."""
    assert HEADER_A["filename"] == HEADER_B["filename"]
    assert save_token(HEADER_A) != save_token(HEADER_B)


def test_renaming_the_file_does_not_move_the_token():
    """A rename is a fact about the directory, not about the world: the same bytes, the same
    playtime and the same write moment are the same world state whatever it is called."""
    assert save_token(_header(filename="keep this one.sav")) == save_token(HEADER_A)


def test_neither_schema_version_moves_the_token(monkeypatch):
    """Where a save token and a timeline row key deliberately part company.

    A row key is a CACHE key and must miss when the code that wrote the row changes; a token
    is an assertion about the world, and expiring a live pin on a server upgrade would refuse
    with a sentence that is not true -- the world did not move.
    """
    before, before_row = save_token(HEADER_A), tl.row_key(HEADER_A)
    monkeypatch.setattr(proj, "SCHEMA_VERSION", proj.SCHEMA_VERSION + 1)
    monkeypatch.setattr(tl, "INDEX_SCHEMA", tl.INDEX_SCHEMA + 1)
    assert save_token(HEADER_A) == before
    assert tl.row_key(HEADER_A) != before_row


def test_every_save_reading_answer_prints_it_because_age_note_leads_with_it():
    """One place, not fifty: ``age_note`` is the line every save-reading tool already
    prints, and the token is the only part of it unique to one world state."""
    note = SaveIdentity(projection={"header": HEADER_A}).age_note
    assert note.startswith(save_token(HEADER_A) + " Han Solo_autosave_0.sav")


def test_a_header_missing_fields_still_gets_a_token():
    """Hand-built headers and pre-``mtime_ns`` projections must not raise on the read path."""
    assert TOKEN_SHAPE.fullmatch(save_token({"session_name": "Han Solo"}))


# ------------------------------------------------------------------ the refusal


def test_a_matching_pin_answers_normally(ledger):
    assert pin.check(HEADER_A, save_token(HEADER_A)) == save_token(HEADER_A)
    assert pin.check(HEADER_A, None) == save_token(HEADER_A)


def test_a_stale_pin_names_both_states_and_the_distance_between_them(ledger):
    """The refusal has to be actionable: which world state was pinned, which one is here
    now, and how far apart they are on BOTH axes -- playtime and wall clock."""
    pinned = pin.check(HEADER_A, None)  # the read that minted it
    with pytest.raises(pin.PinRefused) as caught:
        pin.check(HEADER_B, pinned)
    said = str(caught.value)
    assert pinned in said and save_token(HEADER_B) in said
    assert "121h04m played" in said and "121h22m played" in said
    assert "18m of play and 5m of wall clock apart" in said
    # And it refuses BOTH ways out, because both are wrong: the pinned bytes are gone and
    # the newer save is a different world state.
    assert "Neither one is answerable here" in said
    assert "an autosave overwrites its own file" in said
    assert "Drop as_of=" in said


def test_a_stale_pin_on_a_MANUAL_save_does_not_claim_the_file_was_overwritten(ledger):
    """The confidently-wrong sentence this whole contract exists to avoid, in miniature.

    An autosave is rewritten in place; a manual save is a file the player chose to keep and
    is probably still sitting there. So the manual case says the world moved, and offers the
    one recovery that actually works -- re-read the file, and the pin holds.
    """
    kept = _header(filename="Han Solo_020826-082843.sav")
    later = _header(
        filename="Han Solo_020826-082843.sav",
        play_duration_s=436_920,
        mtime_ns=_WRITTEN_NS + 300 * 10**9,
    )
    with pytest.raises(pin.PinRefused) as caught:
        pin.check(later, pin.check(kept, None))
    said = str(caught.value)
    assert "overwrites its own file" not in said
    assert "the world has moved on since you pinned it" in said
    assert "save='Han Solo_020826-082843.sav' re-reads it" in said


def test_a_token_this_install_never_minted_gets_a_different_refusal(ledger):
    """A stale pin is a fact about the world; an unminted one is a fact about the caller,
    and no amount of detail about the current save explains it."""
    with pytest.raises(pin.PinRefused) as caught:
        pin.check(HEADER_A, "sav:0123456789ab")
    said = str(caught.value)
    assert "no save this install has ever read carries that token" in said
    assert "invented or carried over from another machine" in said
    assert "121h04m played" not in said.split("On disk now is")[0]


def test_a_pin_from_another_world_reports_no_distance_because_there_is_none(ledger):
    """Two worlds keep two unrelated playtime clocks, so subtracting them would print a
    fabricated measurement of how far THIS world has moved."""
    other = _header(save_identifier="ZZZother", session_name="Second Save", filename="s.sav")
    pinned = pin.check(other, None)
    with pytest.raises(pin.PinRefused) as caught:
        pin.check(HEADER_A, pinned)
    said = str(caught.value)
    assert "names a different world" in said
    assert "'Second Save'" in said and "'Han Solo'" in said
    assert "of play and" not in said
    assert "Pass world=" in said


def test_a_string_that_is_not_a_token_at_all_says_what_one_looks_like(ledger):
    with pytest.raises(pin.PinRefused) as caught:
        pin.check(HEADER_A, "Han Solo_autosave_0.sav")
    said = str(caught.value)
    assert "not a save token" in said
    assert "'sav:' followed by 12 hex digits" in said


def test_the_ledger_survives_a_process_boundary(ledger):
    """A pin is minted by one call and handed back by a later one, and the MCP server is a
    long-lived process with a cache directory shared by the web server and the CLI."""
    pin.remember(HEADER_A)
    assert pin.ledger_path().is_file()
    assert pin.recall(save_token(HEADER_A))["filename"] == "Han Solo_autosave_0.sav"
    assert pin.recall("sav:0123456789ab") is None


def test_a_ledger_that_cannot_be_written_costs_a_refusal_and_never_an_answer(
    tmp_path, monkeypatch
):
    """Best-effort, like every other cache here. The pin still holds; only the sharper of
    the two refusals is lost, because an unrecorded token reads as one never minted."""
    monkeypatch.setattr(pin.config, "cache_dir", lambda: tmp_path / "nope" / "deeper")
    assert pin.check(HEADER_A, save_token(HEADER_A)) == save_token(HEADER_A)
    with pytest.raises(pin.PinRefused):
        pin.check(HEADER_B, save_token(HEADER_A))


# --------------------------------------------------------------------- the hazard


@pytest.fixture
def autosaving(monkeypatch, ledger):
    """A save directory the game is writing to: ``load_state`` answers with whatever header
    the test last put on the disk, exactly as a re-resolve after an autosave would."""
    disk = {"header": HEADER_A}

    def load_state(game, path=None, world=None, prefer_manual=False, refresh=False):
        return WorldState(projection={"header": disk["header"]}, game=None)

    monkeypatch.setattr(mcp_app, "load_state", load_state)
    monkeypatch.setattr(mcp_app, "game", lambda: None)
    return disk


def test_the_hazard_a_read_then_an_autosave_then_a_pinned_call(autosaving):
    """THE test this contract exists for, at the chokepoint every save-reading tool goes
    through: read a world state, let the game rewrite the save under the same filename, and
    show that the pinned call REFUSES instead of quietly answering from the newer bytes."""
    first = mcp_app._state()
    pinned = first.token
    assert mcp_app._state(as_of=pinned).token == pinned  # nothing has moved yet

    autosaving["header"] = HEADER_B  # the game autosaves, over the same file

    assert mcp_app._state().token == save_token(HEADER_B)  # unpinned, the newer save answers
    with pytest.raises(pin.PinRefused):
        mcp_app._state(as_of=pinned)


def test_a_pinned_tool_call_returns_the_refusal_instead_of_the_newer_worlds_numbers(
    autosaving,
):
    """End to end through a registered tool, because the guarantee is worthless if it stops
    at the loader: a tool answers from state A, the game writes state B, and the pinned call
    comes back as words rather than as B's figures under A's token."""
    pinned = world_tools._state().token
    autosaving["header"] = HEADER_B

    out = world_tools.world_summary(as_of=pinned)
    assert "that is not the save on disk now" in out
    assert "18m of play and 5m of wall clock apart" in out
    # The refusal must not carry the newer save's token as though it were the answer's:
    # it names it as what is on disk, and says so in the same sentence.
    assert f"on disk now is {save_token(HEADER_B)}" in out


def test_as_of_checks_the_save_that_save_resolved_to_rather_than_competing_with_it(
    autosaving,
):
    """``save=`` picks the file, ``as_of=`` checks what came back -- so pinning a FILENAME
    is no protection at all, which is the reason both exist."""
    pinned = mcp_app._state(save="Han Solo_autosave_0.sav").token
    autosaving["header"] = HEADER_B

    # The filename still resolves, happily, to a world state the caller has never seen.
    assert mcp_app._state(save="Han Solo_autosave_0.sav").token == save_token(HEADER_B)
    with pytest.raises(pin.PinRefused):
        mcp_app._state(save="Han Solo_autosave_0.sav", as_of=pinned)
