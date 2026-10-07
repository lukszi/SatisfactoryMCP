"""Which save this is, and where its players are standing.

The token below is how a client pins an answer to one world state; the contract it takes
part in -- ``as_of=`` and its two refusals -- is ``domain/world/pin.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property

from ...core.saveio.schema import PlayerRecord, Projection, SaveHeader
from ...core.text import ago, format_local_time

__all__ = ["TOKEN_HEX", "TOKEN_PREFIX", "TOKEN_SHAPE", "SaveIdentity", "save_token"]

#: Prefixed so a token handed back cannot be confused with a filename or a world_id.
TOKEN_PREFIX = "sav:"

#: Width of the hash in hex digits: 48 bits, ~2e-5 collision odds at 100,000 saves (§10.1i).
TOKEN_HEX = 12

TOKEN_SHAPE = re.compile(rf"{re.escape(TOKEN_PREFIX)}[0-9a-f]{{{TOKEN_HEX}}}")


def save_token(header: SaveHeader) -> str:
    """A short, stable name for ONE world state, from the save's own header.

    Not the filename, which autosaves recycle, and unlike ``timeline.row_key`` neither
    schema version: those number this server's code, and an upgrade must not expire a live
    pin (docs/mcp-surface.md §10.1i).
    """
    raw = "|".join(
        [
            str(header.get("save_identifier") or f"session:{header.get('session_name')}"),
            str(header.get("play_duration_s")),
            str(header.get("mtime_ns")),
            str(header.get("size")),
        ]
    )
    return TOKEN_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:TOKEN_HEX]


@dataclass
class SaveIdentity:
    """The save's header, the key everything else hangs off, and the pawns in it."""

    projection: Projection

    @property
    def header(self) -> SaveHeader:
        return self.projection.get("header", {})

    @property
    def world_id(self) -> str:
        """Stable per-world key. Labels hang off this, so it must survive autosave
        rotation and renaming -- which saveIdentifier does and the filename does not."""
        h = self.header
        return h.get("save_identifier") or f"session:{h.get('session_name') or '?'}"

    @property
    def token(self) -> str:
        """This world state's token. See ``save_token``."""
        return save_token(self.header)

    @property
    def save_kind(self) -> str:
        name = self.header.get("filename", "").lower()
        return "autosave" if "autosave" in name else "manual save"

    @property
    def age_note(self) -> str:
        """Human-readable provenance, on every response: the token first, as the one part
        unique to a world state, then the file's age, which says when an autosave lags the
        live session; only an autosave earns the "disk may lag" warning."""
        h = self.header
        kind = self.save_kind
        is_autosave = kind == "autosave"
        hours = (h.get("play_duration_s") or 0) / 3600
        written = format_local_time(h.get("mtime_ns"))
        when = f", written {written} ({ago(h.get('mtime_ns'))})" if written else ""
        note = (
            f"{self.token} {h.get('filename', '?')} "
            f"({kind}, world {h.get('session_name', '?')!r}, "
            f"{hours:.0f}h played, saveVersion {h.get('save_version')}{when})"
        )
        if is_autosave:
            note += " -- the game writes autosaves periodically, so disk may lag the live world"
        return note

    @cached_property
    def players(self) -> list[PlayerRecord]:
        """Player pawns with positions.

        Read from Char_Player_C, never BP_PlayerState_C: the state actor sits at the
        world origin, so using it would place every player at (0, 0).
        """
        return [p for p in self.projection.get("players", ()) if p.get("pos")]

    def player_position(self) -> tuple[float, float, float] | None:
        """Where the player is, in centimetres. None if the save has no pawn.

        With several pawns (co-op, or a stale disconnected one) the one holding a
        build gun wins, since that is the one actually being played.
        """
        if not self.players:
            return None
        armed = [p for p in self.players if p.get("has_build_gun")]
        pos = (armed or self.players)[0]["pos"]
        if not pos:
            return None
        x, y, z = pos
        return (float(x), float(y), float(z))
