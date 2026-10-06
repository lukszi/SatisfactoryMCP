"""The wire shapes this package builds: an ask and what it is about, a pin and its ref.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict

__all__ = ["AskAbout", "AskRow", "PinRef", "PinRow"]


class AskAbout(TypedDict):
    """What an ask is about: ``kind`` is plan, process, stage, item or pin; ``plan`` a plan key."""

    kind: str
    label: str
    ref: str
    plan: NotRequired[str | None]
    rev: NotRequired[int | None]


class AskRow(TypedDict):
    """One ask. ``state`` is open, seen or answered; ``answer`` is the one line chat left
    with it ("" for none); ``copy`` is what the page puts on the clipboard; ``plan_name`` is
    ``about.plan`` resolved when read."""

    n: int
    id: str
    text: str
    about: AskAbout
    state: str
    rev: int
    created: float
    seen: float | None
    seen_by: str
    answered: float | None
    answered_by: str
    answer: str
    plan_name: str | None
    copy: str


class PinRef(TypedDict, total=False):
    """What a pin points at. ``resource`` and ``nodes`` are filled by the server for a field."""

    plan: str
    recipe: str
    factory: str
    machine: str
    node: str
    x_m: float
    y_m: float
    resource: str
    nodes: list[str]


class PinRow(TypedDict):
    """One pin as the page shows it. ``selector`` is the canonical text it stands for; a gone
    pin says why in ``gone_why``; ``x_m``/``y_m`` are null for a pin with no place."""

    n: int
    id: str
    kind: str
    ref: PinRef
    label: str
    text: str
    selector: str
    x_m: float | None
    y_m: float | None
    rev: int
    created: float
    gone: bool
    gone_why: str
