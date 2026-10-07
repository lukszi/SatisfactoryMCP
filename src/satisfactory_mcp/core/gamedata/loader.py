"""Read Docs/en-US.json and group it by NativeClass.

The file is UTF-16LE with a BOM, so ``encoding='utf-16'`` (which consumes the BOM)
is required; ``utf-8`` and ``utf-16-le`` both fail. Load is ~40 ms for 10.6 MB.

Docs.json contains no version field of any kind -- ``buildVersion`` lives in the
*save* header, not here -- so cache identity is the file's sha256.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..jsontypes import JsonObject

__all__ = ["DocsDump", "load_docs"]


@dataclass(frozen=True)
class DocsDump:
    """Docs.json grouped by native class, plus its content hash."""

    #: Each class is the dump's own JSON object: ``ClassName`` and its raw ``m*`` fields.
    by_native: dict[str, list[JsonObject]]
    sha256: str
    path: Path
    size: int

    def classes(self, *native_names: str) -> list[JsonObject]:
        """All entries across one or more native classes.

        Several logically-single concepts are split across native classes -- e.g.
        manufacturers live in both FGBuildableManufacturer and
        FGBuildableManufacturerVariablePower -- so lookups are variadic by default.
        """
        out: list[JsonObject] = []
        for name in native_names:
            out.extend(self.by_native.get(name, ()))
        return out

    def index(self, *native_names: str) -> dict[str, JsonObject]:
        """ClassName -> entry. ClassName is the only universal key: FullName is
        absent on 1,316 of 2,868 classes."""
        return {
            name: c
            for c in self.classes(*native_names)
            if isinstance(name := c.get("ClassName"), str)
        }


def _native_name(raw: str) -> str:
    """``/Script/CoreUObject.Class'/Script/FactoryGame.FGRecipe'`` -> ``FGRecipe``."""
    s = raw.strip().rstrip("'")
    if "'" in s:
        s = s.split("'")[-1]
    return s.rsplit(".", 1)[-1]


def load_docs(path: str | Path) -> DocsDump:
    """Load and group Docs.json. Raises FileNotFoundError with a usable message."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(
            f"Docs.json not found at {p}. Set SATISFACTORY_DOCS to "
            r"<install>\CommunityResources\Docs\en-US.json"
        )
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    with open(p, encoding="utf-16") as fh:
        groups = json.load(fh)

    by_native: dict[str, list[JsonObject]] = {}
    for group in groups:
        name = _native_name(group["NativeClass"])
        by_native.setdefault(name, []).extend(group.get("Classes", ()))
    return DocsDump(by_native=by_native, sha256=sha, path=p, size=len(raw))
