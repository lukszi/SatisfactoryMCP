"""The scan a shared export passes before it counts as done: the site is public, so it holds
only the allowlisted files and keys, and none of them may name this machine or its owner.

Text files are read whole; images are checked by their chunk headers, since a PNG text chunk
or a WebP EXIF block is where a path or a name could ride along. docs/map/sharing.md.
"""

from __future__ import annotations

import getpass
import json
import re
import socket
import struct
import subprocess
from collections.abc import Iterable
from pathlib import Path

from mapgen.share.manifest import LIGHT_SCHEMA, MAPS_SCHEMA, SITE_SCHEMA, validate
from satisfactory_mcp.core.jsontypes import JsonValue

__all__ = [
    "ALLOWED_SUFFIXES",
    "TOP_LEVEL",
    "image_problem",
    "manifest_problems",
    "private_words",
    "scan",
    "text_problems",
]

TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".json", ".txt"})
IMAGE_SUFFIXES = frozenset({".png", ".webp"})
#: The only file types the export holds, and the only ones the server config lets through.
ALLOWED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES
#: The only names allowed at the export's root.
TOP_LEVEL = frozenset({"index.html", "assets", "vendor", "data", "t"})
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: Pixel and colour chunks only; tEXt, zTXt, iTXt, eXIf and tIME carry metadata.
PNG_CHUNKS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"gAMA", b"sRGB", b"pHYs"})
#: Bitstream chunks only; EXIF and XMP carry metadata.
WEBP_CHUNKS = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH"})

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


def image_problem(name: str, data: bytes) -> str:
    """Why an image file may not be published, or ``""``: wrong magic, or a metadata chunk."""
    if name.endswith(".png"):
        if not data.startswith(PNG_MAGIC):
            return "not a PNG"
        at = len(PNG_MAGIC)
        while at + 8 <= len(data):
            length, kind = struct.unpack(">I4s", data[at : at + 8])
            if kind not in PNG_CHUNKS:
                return f"a {kind.decode('latin-1')} chunk"
            at += 12 + length
        return ""
    if name.endswith(".webp"):
        if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return "not a WebP"
        at = 12
        while at + 8 <= len(data):
            kind, length = data[at : at + 4], struct.unpack("<I", data[at + 4 : at + 8])[0]
            if kind not in WEBP_CHUNKS:
                return f"a {kind.decode('latin-1')} chunk"
            at += 8 + length + (length & 1)
        return ""
    return "not an image"


def _image_head_problem(path: Path) -> str:
    """``image_problem`` reading only the chunk headers, for files copied by an earlier run."""
    with path.open("rb") as handle:
        head = handle.read(12)
        if path.suffix == ".png":
            if not head.startswith(PNG_MAGIC):
                return "not a PNG"
            handle.seek(len(PNG_MAGIC))
            while chunk := handle.read(8):
                if len(chunk) < 8:
                    return "a truncated chunk"
                length, kind = struct.unpack(">I4s", chunk)
                if kind not in PNG_CHUNKS:
                    return f"a {kind.decode('latin-1')} chunk"
                handle.seek(length + 4, 1)
            return ""
        if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
            return "not a WebP"
        while chunk := handle.read(8):
            if len(chunk) < 8:
                return "a truncated chunk"
            kind, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if kind not in WEBP_CHUNKS:
                return f"a {kind.decode('latin-1')} chunk"
            handle.seek(length + (length & 1), 1)
        return ""


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


def scan(out: Path, roots: Iterable[Path], words: Iterable[str], images: bool = True) -> list[str]:
    """Every reason ``out`` may not be published; empty when it may.

    ``images`` False skips the image chunk walk, for files this run copied and checked already.
    """
    roots, words = list(roots), list(words)
    found: list[str] = []
    for top in sorted(out.iterdir()):
        if top.name not in TOP_LEVEL:
            found.append(f"{top.name}: not part of the shared site")
    for path in sorted(out.rglob("*")):
        rel = path.relative_to(out).as_posix()
        if path.is_symlink():
            found.append(f"{rel}: a link")
            continue
        if path.is_dir():
            continue
        if not SAFE_NAME.match(path.name) or path.suffix not in ALLOWED_SUFFIXES:
            found.append(f"{rel}: a file type the site does not serve")
        elif path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            found.extend(text_problems(rel, text, roots, words))
            if path.suffix == ".json":
                found.extend(manifest_problems(rel, text))
        elif images:
            problem = _image_head_problem(path)
            if problem:
                found.append(f"{rel}: {problem}")
    return found
