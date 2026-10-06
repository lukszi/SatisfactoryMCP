"""Class-side facts: where a class's ``.uasset`` lives, and the defaults read out of it."""

from __future__ import annotations

from ..iostore import IoStore
from .properties import read_float, relative_transform
from .view import PackageView

__all__ = ["MOUNT_ROOTS", "AssetIndex", "ClassFacts"]

#: The mount points a ``/Game/`` or ``/Engine/`` package path can hang off. A container path
#: spells the same asset ``.../FactoryGame/Content/<rest>`` or ``.../Engine/Content/<rest>``,
#: so what the two spellings share is the part AFTER the mount.
MOUNT_ROOTS = ("/Game/", "/Engine/")


class AssetIndex:
    """Container paths for ``.uasset`` classes, by leaf name.

    Every class-side fact a generator wants -- a component template, a creature's
    ``mIsPassiveCreature``, a spore flower's damage radius, an ore's radioactivity -- is read
    out of the class asset, and all of them need the same lookup.

    **A package path is a reference somebody typed and a container path is what the cooker
    wrote, and they agree less often than they look.** They differ over the MOUNT --
    ``/Engine/BasicShapes/Sphere`` contains no ``/Game/`` to split on -- and over CASE, where
    the container spells ``Medkit`` as ``MedKit`` and ``trees`` as ``Trees``. So both halves
    are case-folded and every candidate is kept, with an exact-case leaf still preferred where
    the container offers one.
    """

    SUFFIX = ".uasset"

    def __init__(self, store: IoStore) -> None:
        self.store = store
        self._by_leaf: dict[str, list[str]] = {}
        for path in store.by_path:
            if path.endswith(self.SUFFIX):
                leaf = path[: -len(self.SUFFIX)].replace("\\", "/").rsplit("/", 1)[-1]
                self._by_leaf.setdefault(leaf.lower(), []).append(path)

    @staticmethod
    def _directory(package: str) -> str:
        """A package path's directory below its mount point, case-folded. The mount is dropped
        because it is the part the two spellings genuinely disagree on; what is left is a
        substring of the container path, which is what the namesake guard tests for."""
        directory = package.rsplit("/", 1)[0]
        for root in MOUNT_ROOTS:
            if root in directory:
                return directory.split(root, 1)[-1].strip("/").lower()
        return directory.strip("/").lower()

    def path_for(self, class_package: str) -> str | None:
        """The container path of a class package, guarded against namesakes."""
        leaf = class_package.rsplit("/", 1)[-1]
        candidates = self._by_leaf.get(leaf.lower())
        if not candidates:
            return None
        directory = self._directory(class_package)
        matches = [path for path in candidates if directory in path.replace("\\", "/").lower()]
        if not matches:
            return None
        exact = [
            path
            for path in matches
            if path[: -len(self.SUFFIX)].replace("\\", "/").rsplit("/", 1)[-1] == leaf
        ]
        return (exact or matches)[0]


class ClassFacts:
    """Class-default values read from blueprint ``.uasset`` files, cached per class.

    Two shapes are wanted and both come from the same read: the class DEFAULT OBJECT, whose
    properties are the class's own defaults, and the ``<Name>_GEN_VARIABLE`` component
    templates. The templates are not an optimisation -- a placed instance serialises only
    the parts of a component transform that differ from its template, so without them the
    composed world transform of anything attached under ``BP_WAT2`` is 73 cm out.
    """

    SUFFIX = "_GEN_VARIABLE"

    def __init__(self, store: IoStore, index: AssetIndex) -> None:
        self.store = store
        self.index = index
        self._templates: dict[str, dict[str, tuple]] = {}
        self._defaults: dict[str, dict[str, bytes]] = {}
        self._flags: dict[str, dict[str, bool | None]] = {}
        self._components: dict[str, dict[str, dict[str, bytes]]] = {}
        self._views: dict[str, PackageView | None] = {}
        self._failures: dict[str, str] = {}

    @property
    def resolved(self) -> int:
        return sum(1 for value in self._templates.values() if value)

    @property
    def looked_up(self) -> int:
        return len(self._templates)

    @property
    def failed(self) -> int:
        """Classes whose package is on disk and would not parse.

        A class with no package and a class whose package raised both come out of
        ``templates`` as ``{}``, so without this count a container that cannot be read at
        all reads as a world where nothing has a template. Zero on a healthy install.
        """
        return len(self._failures)

    @property
    def failures(self) -> dict[str, str]:
        """``{class package: exception type}`` for every one of the above, for a report."""
        return dict(self._failures)

    def _view(self, class_package: str) -> PackageView | None:
        if class_package in self._views:
            return self._views[class_package]
        view: PackageView | None = None
        path = self.index.path_for(class_package)
        if path:
            try:
                view = PackageView(self.store.read_path(path))
            except Exception as exc:
                # Recorded, so "no template" and "unreadable package" stay different answers;
                # cached all the same, because re-reading a package that raised buys nothing.
                self._failures[class_package] = type(exc).__name__
        self._views[class_package] = view
        return view

    def _load(self, class_package: str) -> None:
        templates: dict[str, tuple] = {}
        defaults: dict[str, bytes] = {}
        flags: dict[str, bool | None] = {}
        components: dict[str, dict[str, bytes]] = {}
        view = self._view(class_package)
        if view is not None:
            for export in view.exports:
                name = export["name"]
                if name.endswith(self.SUFFIX):
                    stem = name[: -len(self.SUFFIX)]
                    props = view.props(export["slot"])
                    components[stem] = props
                    templates[stem] = relative_transform(props)
                elif name.startswith("Default__") and not defaults:
                    defaults = view.props(export["slot"])
                    flags = {
                        key: view.flag(export["slot"], key) for key in view.kinds(export["slot"])
                    }
        self._templates[class_package] = templates
        self._defaults[class_package] = defaults
        self._flags[class_package] = flags
        self._components[class_package] = components

    def templates(self, class_package: str) -> dict[str, tuple]:
        if class_package not in self._templates:
            self._load(class_package)
        return self._templates[class_package]

    def defaults(self, class_package: str) -> dict[str, bytes]:
        if class_package not in self._defaults:
            self._load(class_package)
        return self._defaults[class_package]

    def flag(self, class_package: str, name: str) -> bool | None:
        if class_package not in self._flags:
            self._load(class_package)
        return self._flags[class_package].get(name)

    def component(self, class_package: str, stem: str) -> dict[str, bytes]:
        if class_package not in self._components:
            self._load(class_package)
        return self._components[class_package].get(stem, {})

    def component_float(self, class_package: str, stem: str, name: str) -> float | None:
        """A float on one of a class's component templates, e.g. a sphere's radius."""
        if class_package not in self._components:
            self._load(class_package)
        for candidate, props in self._components[class_package].items():
            if candidate == stem or candidate.startswith(stem):
                value = props.get(name)
                if value is not None:
                    return read_float(value)
        return None
