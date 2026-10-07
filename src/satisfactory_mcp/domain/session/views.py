"""The shapes this package builds: an ask and what it is about, a pin and its ref, the files
that hold them, the page's focus and a journal entry.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict

__all__ = [
    "AskAbout",
    "AskRecord",
    "AskRow",
    "AsksDoc",
    "FocusDoc",
    "FocusSelection",
    "JournalEntry",
    "PinDescription",
    "PinRecord",
    "PinRef",
    "PinRow",
    "PinsDoc",
]


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


class AskRecord(TypedDict):
    """One ask as the asks file stores it; ``answer`` once chat left a line with it."""

    n: int
    text: str
    about: AskAbout
    rev: int
    created: float
    seen: float | None
    seen_by: str
    answered: float | None
    answered_by: str
    deleted: bool
    answer: NotRequired[str]


class AsksDoc(TypedDict):
    schema: int
    version: int
    next: int
    asks: list[AskRecord]


class PinRecord(TypedDict):
    """One pin as the pins file stores it; ``x_m``/``y_m`` where it stood when made."""

    n: int
    kind: str
    ref: PinRef
    x_m: float | None
    y_m: float | None
    label: str
    rev: int
    created: float
    deleted: bool


class PinsDoc(TypedDict):
    schema: int
    version: int
    next: int
    pins: list[PinRecord]


class PinDescription(TypedDict):
    """A stored pin in words: the selector it stands for, its text, and why it is gone."""

    selector: str
    text: str
    what: str
    x_m: float | None
    y_m: float | None
    gone_why: str


class FocusSelection(TypedDict):
    kind: str
    label: str
    ref: str


class FocusDoc(TypedDict):
    """What the page has open, as the focus file stores it."""

    schema: int
    heartbeat: float
    view: str
    dash: str
    plan: str | None
    rev: int | None
    tab: str
    selection: FocusSelection | None
    follow: str
    sav: str


class JournalEntry(TypedDict):
    """One line of an activity journal; ``actor`` is ``Actor.to_dict``'s."""

    id: str
    seq: int
    ts: float
    actor: dict[str, str | int]
    sav: str
    kind: str
    tool: str
    plan: str | None
    rev: int | None
    args: dict[str, object] | None
    text: str
