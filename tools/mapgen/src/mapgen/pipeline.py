"""The renders orchestrator: ``python -m mapgen renders``.

Stub. The move phase brings ``main`` and ``LAYERS`` here from ``tools/gen_map_renders.py``
unchanged. ``Refusal`` and ``Plan`` are the shapes the later split of ``main``
into ``plan()`` and ``run()`` will use, and nothing raises or builds them yet.
"""

from __future__ import annotations

from dataclasses import dataclass


class Refusal(Exception):
    """A run the generator will not do, with the exit code ``main`` returns for it."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Step:
    id: str
    cached: bool
    est_s: float


@dataclass(frozen=True)
class Plan:
    layers: tuple[str, ...]
    size: int
    steps: tuple[Step, ...]
