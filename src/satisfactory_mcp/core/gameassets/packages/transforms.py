"""Where a placed component is: rotators, quaternions and the ``AttachParent`` chain.

UE composes parent * child, and rotators are pitch/yaw/roll in degrees.
"""

from __future__ import annotations

import math

from .classfacts import ClassFacts
from .properties import relative_transform
from .view import PackageView

__all__ = [
    "compose",
    "local_transform",
    "quat_mul",
    "quat_rotate",
    "root_component",
    "rotator_to_quat",
    "world_transform",
]

_D2R = math.pi / 180.0
_UNIT_SCALE = (1.0, 1.0, 1.0)


def rotator_to_quat(pitch: float, yaw: float, roll: float) -> tuple[float, float, float, float]:
    sp, cp = math.sin(pitch * _D2R * 0.5), math.cos(pitch * _D2R * 0.5)
    sy, cy = math.sin(yaw * _D2R * 0.5), math.cos(yaw * _D2R * 0.5)
    sr, cr = math.sin(roll * _D2R * 0.5), math.cos(roll * _D2R * 0.5)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def quat_mul(a: tuple, b: tuple) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(q: tuple, v: tuple) -> tuple[float, float, float]:
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def compose(parent: tuple, child: tuple) -> tuple:
    parent_location, parent_rotation, parent_scale = parent
    child_location, child_rotation, child_scale = child
    scaled = (
        child_location[0] * parent_scale[0],
        child_location[1] * parent_scale[1],
        child_location[2] * parent_scale[2],
    )
    rotated = quat_rotate(parent_rotation, scaled)
    return (
        (
            parent_location[0] + rotated[0],
            parent_location[1] + rotated[1],
            parent_location[2] + rotated[2],
        ),
        quat_mul(parent_rotation, child_rotation),
        (
            parent_scale[0] * child_scale[0],
            parent_scale[1] * child_scale[1],
            parent_scale[2] * child_scale[2],
        ),
    )


def _owner_class(view: PackageView, component: int) -> str | None:
    """The blueprint class of the actor owning ``component``, whose templates hold defaults."""
    owner = view.outer_of.get(component)
    if owner is None:
        return None
    path = view.class_of.get(owner)
    return path if path and path.startswith("/Game/") else None


def local_transform(view: PackageView, slot: int, classes: ClassFacts) -> tuple:
    """A component's own ``(location, quaternion, scale)``, template defaults filled in."""
    props = view.props(slot)
    defaults: tuple = (None, None, None)
    owner = _owner_class(view, slot)
    if owner:
        template = classes.templates(owner).get(view.exports[slot]["name"])
        if template:
            defaults = template
    location, rotation, scale = relative_transform(props, defaults)
    return (
        location or (0.0, 0.0, 0.0),
        rotator_to_quat(*(rotation or (0.0, 0.0, 0.0))),
        scale or _UNIT_SCALE,
    )


def world_transform(
    view: PackageView,
    slot: int,
    classes: ClassFacts,
    seen: set[int] | None = None,
) -> tuple[tuple | None, int | None]:
    """Compose up the ``AttachParent`` chain. Returns the transform and the parent actor.

    The parent actor is the export the immediate ``AttachParent`` belongs to when that is a
    different actor from this one -- which is how a Mercer shrine names the sphere it is
    the pedestal of, exactly, without a distance heuristic.
    """
    if seen is None:
        seen = set()
    if slot in seen or len(seen) > 24:
        return None, None
    seen.add(slot)
    here = local_transform(view, slot, classes)
    payload = view.props(slot).get("AttachParent")
    if payload is None:
        return here, None
    parent = view.export_ref(payload)
    if parent is None or not 0 <= parent < len(view.exports):
        return here, None
    my_actor = view.outer_of.get(slot)
    parent_actor = view.outer_of.get(parent)
    attached_to = parent_actor if parent_actor is not None and parent_actor != my_actor else None
    above, deeper = world_transform(view, parent, classes, seen)
    if above is None:
        return None, attached_to or deeper
    return compose(above, here), attached_to if attached_to is not None else deeper


def root_component(view: PackageView, actor: int) -> int | None:
    """The actor's root ``SceneComponent``, by property if it has one and by shape if not."""
    payload = view.props(actor).get("RootComponent")
    if payload is not None:
        slot = view.export_ref(payload)
        if slot is not None and 0 <= slot < len(view.exports):
            return slot
    candidates = [
        child for child in view.children.get(actor, []) if "RelativeLocation" in view.props(child)
    ]
    unattached = [child for child in candidates if "AttachParent" not in view.props(child)]
    if len(unattached) == 1:
        return unattached[0]
    if len(candidates) == 1:
        return candidates[0]
    return None
