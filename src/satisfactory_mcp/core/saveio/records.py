"""Readings every projection consumer shares: the machine groups, leaf names, actor classes."""

from __future__ import annotations

from collections.abc import Iterator

__all__ = ["MACHINE_GROUPS", "actor_class", "instance_leaf", "iter_machine_records"]

#: The projection keys that hold placed machines, in the order a census reads them.
MACHINE_GROUPS = ("machines", "extractors", "generators")


def instance_leaf(instance: object) -> str:
    """The short actor name at the end of an instance path; ``""`` for none."""
    return str(instance or "").rsplit(".", 1)[-1]


def actor_class(actor: str) -> str:
    """Buildable class from an actor's short instance name.

    ``Build_ConstructorMk1_C_2147441119`` -> ``Build_ConstructorMk1_C``. The trailing
    id is what makes an actor unique across saves, so it must be stripped to get the
    class and kept to get identity.
    """
    parts = actor.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return actor


def iter_machine_records(projection: dict) -> Iterator[tuple[str, str, dict]]:
    """Every machine, extractor and generator record as ``(group, leaf, record)``."""
    for group in MACHINE_GROUPS:
        for record in projection.get(group, ()):
            yield group, instance_leaf(record.get("instance")), record
