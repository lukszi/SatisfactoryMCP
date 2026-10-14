"""The scan a shared export passes before it counts as done: the site is public, so it holds
only the allowlisted files and keys, and none of them may name this machine or its owner.

Text files are read whole; images are judged by their names only and never opened: a tile
folder holds hundreds of thousands of them, all written by the renderer. docs/map/sharing.md.
"""

from __future__ import annotations

import getpass
import json
import re
import socket
import subprocess
from collections.abc import Iterable
from pathlib import Path

from mapgen.share.manifest import LIGHT_SCHEMA, MAPS_SCHEMA, SITE_SCHEMA, validate
from satisfactory_mcp.core.jsontypes import JsonValue

__all__ = [
    "ALLOWED_SUFFIXES",
    "TOP_LEVEL",
    "file_problems",
    "manifest_problems",
    "private_words",
    "scan",
    "text_problems",
    "top_problems",
]

TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".json", ".txt"})
IMAGE_SUFFIXES = frozenset({".png", ".webp"})
#: The only file types the export holds, and the only ones the server config lets through.
ALLOWED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES
#: The only names allowed at the export's root.
TOP_LEVEL = frozenset({"index.html", "assets", "vendor", "data", "t"})
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")

#: Patterns no file may contain, with what each one means.
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b[a-z]:[\\/]{1,2}(users|documents and settings)[\\/]"),
        "a Windows home path",
    ),
    (re.compile(r"(?<![\w.])/(home|Users|root)/[A-Za-z0-9._-]+"), "a Unix home path"),
    (re.compile(r"(?i)\b[a-z]:\\\\?[a-z0-9 _.-]+\\\\?"), "a Windows path"),
    (
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
        "an email address",
    ),
    (re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])"), "a full git or SHA-1 hash"),
    (re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])"), "a full SHA-256 digest"),
    (re.compile(r"(?i)\bfile:/"), "a file URL"),
)

#: In the built page: what a strict Content-Security-Policy would refuse, or what loads from
#: off the site. Links (Leaflet's own credit) are not loads, so origins are checked in HTML.
PAGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"""\sstyle=["']"""), "a style attribute"),
    (re.compile(r"""\son[a-z]+=["']"""), "an inline event handler"),
    (re.compile(r"sourceMappingURL"), "a source map reference"),
    (re.compile(r"\beval\(|new Function\("), "code built from a string"),
    (re.compile(r"serviceWorker|importScripts"), "a worker"),
)
HTML_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<script(?![^>]*\bsrc=)[^>]*>"), "an inline script"),
    (re.compile(r"<style\b"), "an inline style block"),
    (
        re.compile(r"""<(?:script|link|img|iframe)\b[^>]*(?:src|href)=["'](?:[a-z]+:)?//"""),
        "a file from another origin",
    ),
)


def private_words(extra: Iterable[str] = ()) -> list[str]:
    """This machine's user and host names, git's author name and email, and ``extra``."""
    words = {getpass.getuser(), socket.gethostname(), *extra}
    for key in ("user.name", "user.email"):
        try:
            found = subprocess.run(
                ["git", "config", key], capture_output=True, text=True, timeout=10, check=False
            ).stdout.strip()
        except OSError:
            found = ""
        words.update(found.replace("@", " ").split() if found else [])
    return sorted({w for w in words if len(w) >= 3 and w.lower() not in {"root", "user", "admin"}})


def text_problems(rel: str, text: str, roots: Iterable[Path], words: Iterable[str]) -> list[str]:
    """What is wrong with one text file of the export."""
    found = [f"{rel}: {what}" for pattern, what in PATTERNS if pattern.search(text)]
    low = text.lower()
    for root in roots:
        for spelled in {str(root), root.as_posix()}:
            if spelled.lower() in low:
                found.append(f"{rel}: the local folder {root.name}")
    for word in words:
        if re.search(r"(?i)(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text):
            found.append(f"{rel}: the local user, host or author name {word!r}")
    if rel.endswith((".html", ".js")):
        found.extend(f"{rel}: {what}" for pattern, what in PAGE_PATTERNS if pattern.search(text))
    if rel.endswith(".html"):
        found.extend(f"{rel}: {what}" for pattern, what in HTML_PATTERNS if pattern.search(text))
    return found


def _layer_light_keys(site: JsonValue) -> list[str]:
    """Each layer's ``X-Map-Light`` header, a JSON string inside the JSON, against its list."""
    found: list[str] = []
    layers = site.get("layers") if isinstance(site, dict) else None
    for ident, row in (layers if isinstance(layers, dict) else {}).items():
        headers = row.get("headers") if isinstance(row, dict) else None
        raw = headers.get("X-Map-Light") if isinstance(headers, dict) else None
        if isinstance(raw, str):
            light: JsonValue = json.loads(raw)
            found.extend(validate(light, LIGHT_SCHEMA, f"site.json/{ident}/light"))
    return found


def manifest_problems(rel: str, text: str) -> list[str]:
    doc: JsonValue = json.loads(text)
    if rel == "data/maps.json":
        return validate(doc, MAPS_SCHEMA, "maps.json")
    if rel == "data/site.json":
        return validate(doc, SITE_SCHEMA, "site.json") + _layer_light_keys(doc)
    return [f"{rel}: a JSON file that is not one of the two manifests"]


def top_problems(names: Iterable[str]) -> list[str]:
    """Each top-level name that is not part of the site."""
    return [f"{n}: not part of the shared site" for n in sorted(set(names)) if n not in TOP_LEVEL]


def file_problems(rel: str, path: Path, roots: list[Path], words: list[str]) -> list[str]:
    """What is wrong with one file of the export; only text files are opened."""
    if path.is_symlink():
        return [f"{rel}: a link"]
    if not SAFE_NAME.match(path.name) or path.suffix not in ALLOWED_SUFFIXES:
        return [f"{rel}: a file type the site does not serve"]
    if path.suffix not in TEXT_SUFFIXES:
        return []
    text = path.read_text(encoding="utf-8")
    found = text_problems(rel, text, roots, words)
    if path.suffix == ".json":
        found.extend(manifest_problems(rel, text))
    return found


def scan(out: Path, roots: Iterable[Path], words: Iterable[str]) -> list[str]:
    """Every reason ``out`` may not be published; empty when it may."""
    roots, words = list(roots), list(words)
    found = top_problems(top.name for top in out.iterdir())
    for path in sorted(out.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        found.extend(file_problems(path.relative_to(out).as_posix(), path, roots, words))
    return found
