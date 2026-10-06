"""Save inputs for the extractor tests: the machine's save folder and stand-ins for parsed actors."""

from __future__ import annotations

from pathlib import Path

import pytest

from pioneersav import ParseError, Reader, read_trailer
from pioneersav.trailers import CONVEYOR_CHAIN
from satisfactory_mcp import config
from tests.support.paths import FIXTURES

#: Records that carry a placed building's own transform.
PLACED = ("machines", "extractors", "generators")


def saves_root_or_skip() -> Path:
    """The save directory the server would read, or a skip when this machine has none."""
    root = config.saves_root()
    if not root.is_dir():
        pytest.skip("no save directory on this machine")
    return root


class Chain:
    """A stand-in conveyor chain: ``_belts`` reads nothing off an actor but this attribute.

    The camel case is the parser's spelling: ``ParsedObject`` exposes ``actorSpecificInfo``.
    """

    def __init__(self, info) -> None:
        self._info = info

    @property
    def actorSpecificInfo(self):
        return self._info


class UnreadableChain(Chain):
    """A chain whose trailing bytes raise on access, the way a torn trailer does."""

    @property
    def actorSpecificInfo(self):
        raise ParseError("at body offset 0: conveyor chain left 12 trailing bytes unread")


def trailer_chains() -> list:
    """The real conveyor chain records out of ``fixtures/save_trailers.bin``.

    The container is an int32 count, then per entry a length-prefixed class path, an int32
    length and that many bytes (``tests/pioneersav/test_trailers.py``).
    """
    path = FIXTURES / "save_trailers.bin"
    if not path.is_file():
        pytest.skip("trailer fixture not committed")
    reader = Reader(path.read_bytes())
    chains = []
    for _ in range(reader.i32()):
        cls = reader.string()
        blob = reader.bytes(reader.i32())
        if cls == CONVEYOR_CHAIN:
            chains.append(read_trailer(CONVEYOR_CHAIN, blob, 0, len(blob)))
    return chains
