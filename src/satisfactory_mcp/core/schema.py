"""Data-file schema guards: refuse a file a newer version wrote, and say which version writes.

docs/releasing.md "Compatibility rules" has the policy this module enforces.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from .jsontypes import is_object_dict

__all__ = ["NewerSchema", "check", "writer_version"]


class NewerSchema(RuntimeError):
    """A data file carries a schema this version cannot read."""

    def __init__(self, path: Path | str, found: int, known: int) -> None:
        super().__init__(
            f"{path} was written by a newer version of satisfactory-mcp (schema {found}; this "
            f"version reads up to {known}). Upgrade satisfactory-mcp; the file is left as it is."
        )
        self.path, self.found, self.known = Path(path), found, known


def check(raw: object, known: int, path: Path | str) -> None:
    """Raise ``NewerSchema`` when ``raw["schema"]`` is above ``known``; absent means old."""
    if not is_object_dict(raw):
        return
    found = raw.get("schema")
    if isinstance(found, bool) or not isinstance(found, int):
        return
    if found > known:
        raise NewerSchema(path, found, known)


def writer_version() -> str:
    """The installed package version, or ``"0"`` when running from an uninstalled tree."""
    try:
        return metadata.version("satisfactory-mcp")
    except metadata.PackageNotFoundError:
        return "0"
