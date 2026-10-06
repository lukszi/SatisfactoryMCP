"""Which save this is, and where its players are standing.

The token below is how a client pins an answer to one world state; the contract it takes
part in -- ``as_of=`` and its two refusals -- is ``domain/world/pin.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property

from ...core.text import ago, format_local_time

__all__ = ["TOKEN_HEX", "TOKEN_PREFIX", "TOKEN_SHAPE", "SaveIdentity", "save_token"]

#: How a save token is spelled. Prefixed so it is self-identifying wherever it lands: a
#: caller handing one back must not be able to confuse it with a filename or a world_id,
#: which are the other two strings this surface accepts as "which save".
TOKEN_PREFIX = "sav:"

#: Width of the hash, in hex digits -- 48 bits.
#:
#: The bound that matters is a birthday one over every state one install has ever minted. At
#: 100,000 saves (five minutes apart, that is a year of unbroken play) the chance that any
#: two of them share a token is about 2e-5. Six digits is the width that LOOKS right and is
#: 24 bits: even odds of a collision by 5,000 saves, which one long-lived install reaches.
TOKEN_HEX = 12

TOKEN_SHAPE = re.compile(rf"{re.escape(TOKEN_PREFIX)}[0-9a-f]{{{TOKEN_HEX}}}")


def save_token(header: dict) -> str:
    """A short, stable name for ONE world state, from the save's own header.

    The FILENAME is excluded, because the game recycles ``autosave_0``/``_1``/``_2``: a name
    alone names three worlds an hour apart and, later the same session, three others.
    ``mtime_ns`` and ``size`` are what separate two states that share one.

    NEITHER schema version is in it, unlike ``timeline.row_key``. Both number this server's
    code rather than the world, so folding them in would expire a live pin on an upgrade --
    and the refusal that expiry produces has no true sentence to offer, because the world did
    not move. A row key is a cache key and must miss when the code changes; this is an
    assertion about the world and must not.
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

    projection: dict

    @property
    def header(self) -> dict:
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
        """Human-readable provenance. Always shown: autosaves rotate every ~5 min
        and can catch the factory mid-restructure.

        The mtime rides in every response since a client was twice told "nothing is
        here" by an autosave hours behind the live session -- the file's age is the
        one number that would have said so, and it was on disk the whole time. The
        autosave clause is a WARNING, not decoration: a manual save is a moment the
        player chose, an autosave is whenever the timer last fired, so only the
        latter earns "disk may lag the world".

        The token LEADS, because it is the only part of this line that is unique to one
        world state: everything after it, filename included, is shared by every autosave
        the file has ever held.
        """
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
    def players(self) -> list[dict]:
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
        pick = (armed or self.players)[0]
        x, y, z = pick["pos"]
        return (float(x), float(y), float(z))
