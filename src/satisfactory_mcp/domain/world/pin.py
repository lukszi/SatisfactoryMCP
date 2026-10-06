"""``as_of=``: a caller pins one world state, and every later call is consistent or refuses.

``identity.save_token`` is the name that does not repeat across autosave rotation; this module
is what happens when a caller hands one back. The contract is docs/mcp-surface.md §10.1i.
"""

from __future__ import annotations

import json
from pathlib import Path

from typing_extensions import TypedDict

from ... import config
from ...core import atomic
from ...core.saveio.schema import SaveHeader
from ...core.text import format_gap, format_local_time, format_playtime
from .identity import TOKEN_SHAPE, save_token

__all__ = [
    "LEDGER_MAX",
    "PinEntry",
    "PinRefused",
    "check",
    "ledger_path",
    "recall",
    "remember",
]


class PinEntry(TypedDict):
    """What the ledger keeps about one minted token: the save it named."""

    world_id: str
    session_name: str
    filename: str
    play_duration_s: int
    mtime_ns: int


class PinRefused(RuntimeError):
    """``as_of=`` named a world state that is not the one on disk: a refusal, never a
    fallback to the newer save, which would blend two states into one answer."""


#: Tokens kept in the ledger, newest by write time: ~17 hours of play at the ~5 minute
#: autosave, in a few tens of kB.
LEDGER_MAX = 200


def ledger_path() -> Path:
    """Where the tokens this install has minted are recorded."""
    return config.cache_dir() / "save-pins.json"


def _load() -> dict[str, PinEntry]:
    try:
        raw = json.loads(ledger_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries: dict[str, PinEntry] = raw if isinstance(raw, dict) else {}
    return entries


def remember(header: SaveHeader) -> str:
    """Record this save under its token, and return the token.

    Called on every read, because a pin is recognisable only if the read that MINTED it was
    recorded. Best-effort and last writer wins across processes: a lost entry costs one
    refusal its detail, never an answer.
    """
    token = save_token(header)
    entries = _load()
    if token in entries:
        return token
    entries[token] = {
        "world_id": header.get("save_identifier"),
        "session_name": header.get("session_name"),
        "filename": header.get("filename"),
        "play_duration_s": header.get("play_duration_s"),
        "mtime_ns": header.get("mtime_ns"),
    }
    if len(entries) > LEDGER_MAX:
        kept = sorted(entries.items(), key=lambda kv: kv[1].get("mtime_ns") or 0)
        entries = dict(kept[-LEDGER_MAX:])
    try:
        atomic.write_bytes(
            ledger_path(), json.dumps(entries, separators=(",", ":")).encode("utf-8")
        )
    except OSError:
        pass
    return token


def recall(token: str) -> PinEntry | None:
    """What this install recorded about a token, or ``None`` if it never minted one."""
    found = _load().get(token)
    return found if isinstance(found, dict) else None


def _describe(entry: PinEntry | SaveHeader) -> str:
    """One save as the three facts a reader compares two states on."""
    written = format_local_time(entry.get("mtime_ns"))
    when = f", written {written}" if written else ""
    return (
        f"{entry.get('filename') or '?'} "
        f"({format_playtime(entry.get('play_duration_s'))} played{when})"
    )


def _distance(pinned: PinEntry, current: SaveHeader) -> str:
    """How far apart two states are, on both axes, unsigned.

    Unsigned because the absolute readings are printed either side of this and show the
    direction; a signed phrase would have to name a direction per axis, and the two axes can
    disagree -- loading an older save moves wall clock forward and playtime back.
    """
    play = (current.get("play_duration_s") or 0) - (pinned.get("play_duration_s") or 0)
    wall = ((current.get("mtime_ns") or 0) - (pinned.get("mtime_ns") or 0)) / 1e9
    return f"{format_gap(play)} of play and {format_gap(wall)} of wall clock apart"


#: The way out, said the same way by every refusal: there is exactly one that always works,
#: and a caller told three spellings of it will try the wrong two first.
_WAY_OUT = "Drop as_of= to read the save on disk now, and pin the token that answer prints."


def _autosave(entry: PinEntry) -> bool:
    return "autosave" in (entry.get("filename") or "").lower()


def _whether_gone(pinned: PinEntry) -> str:
    """Why the pinned state cannot simply be re-read -- and only where that is TRUE.

    An autosave is rewritten in place, so its bytes are very likely gone. A manual save is a
    file the player chose to keep and is probably still sitting there; claiming it was
    overwritten would be the confidently-wrong sentence this whole contract exists to avoid.
    """
    if _autosave(pinned):
        return (
            "an autosave overwrites its own file, so the state you pinned is very likely "
            "no longer on disk"
        )
    return "the world has moved on since you pinned it"


def _recover(pinned: PinEntry) -> str:
    """The second way out, offered only where it can work.

    Re-reading the pinned FILE is the right move for a manual save and closes the loop
    honestly: if the bytes are unchanged the answer carries the pinned token and this same
    check passes. Offering it for an autosave would send the caller after bytes that are
    gone, so it is not offered.
    """
    if _autosave(pinned):
        return ""
    return (
        f"That file may still be on disk: save={pinned.get('filename')!r} re-reads it, and "
        f"the pin holds if the bytes have not moved."
    )


def check(header: SaveHeader, as_of: str | None) -> str:
    """Record this read, and enforce ``as_of=`` against it. Returns the current token.

    Raises ``PinRefused`` in the four ways a pin can fail, four sentences for four mistakes
    (§10.1i). Each opens by naming the pin, because every tool wraps it in "could not read
    save:" and the save that could not be read is the PINNED one.
    """
    current = remember(header)
    if not as_of:
        return current
    want = as_of.strip()
    if want == current:
        return current

    here = f"On disk now is {current}, {_describe(header)}."
    if not TOKEN_SHAPE.fullmatch(want):
        raise PinRefused(
            f"as_of={want!r}: not a save token. A token is 'sav:' followed by 12 hex digits "
            f"and is printed at the start of every save-reading answer. {here} {_WAY_OUT}"
        )
    pinned = recall(want)
    if pinned is None:
        raise PinRefused(
            f"as_of={want}: no save this install has ever read carries that token. A token "
            f"is minted here from the save it names, so one that is unknown was either "
            f"invented or carried over from another machine. {here} {_WAY_OUT}"
        )
    # A pin from ANOTHER world gets no ``_distance``: two worlds' playtimes are unrelated
    # clocks, and subtracting them would read as how far this world has moved.
    if pinned.get("world_id") != header.get("save_identifier"):
        raise PinRefused(
            f"as_of={want}: that token names a different world. You pinned "
            f"{pinned.get('session_name') or '?'!r}, {_describe(pinned)}; this call resolved "
            f"to {header.get('session_name') or '?'!r}, {current}, {_describe(header)}. Two "
            f"worlds keep two unrelated playtime clocks, so there is no distance to report. "
            f"Pass world= to say which one you mean, and pin the token that answer prints."
        )
    tail = " ".join(part for part in (_recover(pinned), _WAY_OUT) if part)
    raise PinRefused(
        f"as_of={want}: that is not the save on disk now. You pinned {_describe(pinned)}; "
        f"on disk now is {current}, {_describe(header)} -- {_distance(pinned, header)}. "
        f"Neither one is answerable here: {_whether_gone(pinned)}, and answering from the "
        f"newer save would put two different world states into one conclusion. {tail}"
    )
