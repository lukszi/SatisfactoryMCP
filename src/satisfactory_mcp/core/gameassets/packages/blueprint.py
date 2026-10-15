"""A blueprint class's components, placed in the actor's own frame.

The construction-script tree (``SCS_Node`` exports) says which component template hangs off
which; a parent blueprint contributes its own tree first, and an ``InheritableComponentHandler``
template overrides a parent component by name. Meshes placed as lightweight instances live in
a separate ``AbstractInstanceDataObject`` the class default names. docs/building-shapes.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..iostore import IoStore
from .classfacts import AssetIndex
from .properties import Vec3, property_tags, read_triple, relative_transform
from .transforms import Quat, Transform, compose, rotator_to_quat
from .view import PackageView, class_name_of
from .zen import ScriptObjects

__all__ = ["IDENTITY", "Component", "class_components"]

IDENTITY: Transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))

_TEMPLATE_SUFFIX = "_GEN_VARIABLE"
#: Where ``SuperIndex`` sits in a 72-byte ``FExportMapEntry``: after offset, size, name,
#: outer and class, five 8-byte fields.
_EXPORT_SUPER_AT = 40
_EXPORT_ENTRY_BYTES = 72
#: Deep enough for any blueprint chain the game ships; a loop stops here rather than recursing.
_MAX_DEPTH = 8


@dataclass(frozen=True)
class Component:
    """One component template: its name, class (short), placement and raw properties."""

    name: str
    cls: str
    transform: Transform
    props: dict[str, bytes]
    view: PackageView

    def ref(self, key: str) -> str | None:
        """An object property pointing out of the package, as a path."""
        payload = self.props.get(key)
        return self.view.import_path(payload) if payload is not None else None

    def fname(self, key: str) -> str | None:
        payload = self.props.get(key)
        return self.view.read_fname(payload) if payload is not None else None


def _super_path(view: PackageView, slot: int) -> str | None:
    pkg = view.pkg
    at = pkg.export_offset + slot * _EXPORT_ENTRY_BYTES + _EXPORT_SUPER_AT
    return view.object_path(struct.unpack_from("<Q", pkg.blob, at)[0])


def _local(props: dict[str, bytes], base: Transform | None = None) -> Transform:
    """A template's relative transform, falling back to ``base``'s parts where unset."""
    location, rotation, scale = relative_transform(props)
    base_location, base_rotation, base_scale = base or IDENTITY
    return (
        location or base_location,
        rotator_to_quat(*rotation) if rotation else base_rotation,
        scale or base_scale,
    )


def _fname_payload(view: PackageView, props: dict[str, bytes], key: str) -> str | None:
    payload = props.get(key)
    return view.read_fname(payload) if payload is not None else None


def _child_slots(view: PackageView, props: dict[str, bytes]) -> list[int]:
    payload = props.get("ChildNodes")
    if not payload or len(payload) < 4:
        return []
    count = struct.unpack_from("<I", payload, 0)[0]
    slots = (view.export_ref(payload[4 + 4 * i : 8 + 4 * i]) for i in range(count))
    return [slot for slot in slots if slot is not None]


class _Walk:
    """Components gathered across one class and its blueprint ancestors."""

    def __init__(self, store: IoStore, scripts: ScriptObjects, index: AssetIndex) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self.components: dict[str, Component] = {}
        self.instances: list[Component] = []

    def open(self, package: str) -> PackageView | None:
        path = self.index.path_for(package)
        if not path:
            return None
        try:
            return PackageView(self.store.read_path(path), self.scripts)
        except Exception:
            return None

    def visit(self, package: str, depth: int = 0) -> bool:
        view = self.open(package)
        if view is None or depth > _MAX_DEPTH:
            return False
        leaf = package.rsplit("/", 1)[-1] + "_C"
        own = next((e for e in view.exports if e["name"] == leaf), None)
        parent = _super_path(view, own["slot"]) if own is not None else None
        if parent and parent.startswith("/Game/"):
            self.visit(parent, depth + 1)
        self._overrides(view)
        self._tree(view)
        self._instance_data(view)
        return True

    def _overrides(self, view: PackageView) -> None:
        """Inherited templates a child re-states: only what it serialises changes."""
        for export in view.exports:
            stem = export["name"].removesuffix(_TEMPLATE_SUFFIX)
            owner = view.outer_of.get(export["slot"])
            owner_cls = view.class_of.get(owner) if owner is not None else None
            if (
                stem in self.components
                and class_name_of(owner_cls) == "InheritableComponentHandler"
            ):
                old = self.components[stem]
                props = {**old.props, **view.props(export["slot"])}
                local = _local(view.props(export["slot"]), old.transform)
                self.components[stem] = Component(stem, old.cls, local, props, view)

    def _tree(self, view: PackageView) -> None:
        nodes = [
            e["slot"] for e in view.exports if class_name_of(view.class_of[e["slot"]]) == "SCS_Node"
        ]
        children = {kid for slot in nodes for kid in _child_slots(view, view.props(slot))}
        for slot in nodes:
            if slot in children:
                continue
            parent_name = _fname_payload(view, view.props(slot), "ParentComponentOrVariableName")
            inherited = self.components.get(parent_name or "")
            self._node(view, slot, inherited.transform if inherited else IDENTITY, 0)

    def _node(self, view: PackageView, slot: int, above: Transform, depth: int) -> None:
        if depth > 32:
            return
        props = view.props(slot)
        template = view.export_ref(props.get("ComponentTemplate", b""))
        name = _fname_payload(view, props, "InternalVariableName") or ""
        placed = above
        if template is not None and 0 <= template < len(view.exports):
            template_props = view.props(template)
            placed = compose(above, _local(template_props))
            cls = class_name_of(view.class_of.get(template))
            self.components[name] = Component(name, cls, placed, dict(template_props), view)
        for kid in _child_slots(view, props):
            self._node(view, kid, placed, depth + 1)

    def _instance_data(self, view: PackageView) -> None:
        """Meshes the class places as lightweight instances, which replace a parent's."""
        found: list[Component] = []
        for export in view.exports:
            if class_name_of(view.class_of[export["slot"]]) != "AbstractInstanceDataObject":
                continue
            payload = view.props(export["slot"]).get("Instances")
            if not payload or len(payload) < 4:
                continue
            count, pos = struct.unpack_from("<I", payload, 0)[0], 4
            for number in range(count):
                tags, pos = property_tags(payload, view.pkg.names, pos)
                fields = {tag.name: tag.payload for tag in tags if tag.name}
                transform = _instance_transform(view, fields.get("RelativeTransform", b""))
                name = f"Instance{number}"
                found.append(Component(name, "AbstractInstance", transform, fields, view))
        if found:
            self.instances = found


def _quat(payload: bytes) -> Quat | None:
    if len(payload) == 32:
        x, y, z, w = struct.unpack("<4d", payload)
        return (x, y, z, w)
    if len(payload) == 16:
        x, y, z, w = struct.unpack("<4f", payload)
        return (x, y, z, w)
    return None


def _instance_transform(view: PackageView, payload: bytes) -> Transform:
    """An ``FTransform`` serialised as tags: ``Rotation`` quaternion, ``Translation``, ``Scale3D``."""
    tags, _end = property_tags(payload, view.pkg.names, 0)
    fields = {tag.name: tag.payload for tag in tags if tag.name}
    location: Vec3 = read_triple(fields.get("Translation", b"")) or IDENTITY[0]
    scale: Vec3 = read_triple(fields.get("Scale3D", b"")) or IDENTITY[2]
    return (location, _quat(fields.get("Rotation", b"")) or IDENTITY[1], scale)


def class_components(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, class_package: str
) -> list[Component] | None:
    """Every component of a blueprint class in its actor frame, or ``None`` with no package.

    Templates come first in construction order, then the lightweight mesh instances.
    """
    walk = _Walk(store, scripts, index)
    if not walk.visit(class_package):
        return None
    return [*walk.components.values(), *walk.instances]
