"""The extractor's command line: argv in, one JSON object on stdout, an exit code out.

Diagnostics go to stderr so stdout stays parseable.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from ..projection import SCHEMA_VERSION
from .parser import PARSE_ERROR, header_info
from .walk import extract_projection

__all__ = ["list_dir", "main"]


def list_dir(root: str) -> dict:
    """Header-only scan of every .sav under ``root``, in ONE process.

    Header parsing is milliseconds and process startup is not. Unsupported saves are bucketed
    with their reason rather than aborting the scan.
    """
    saves: list[dict] = []
    unsupported: list[dict] = []
    for path in sorted(Path(root).rglob("*.sav")):
        try:
            saves.append(header_info(str(path)))
        except Exception as exc:
            file_stat = path.stat()
            unsupported.append(
                {
                    "path": str(path),
                    "filename": path.name,
                    "reason": str(exc),
                    "mtime_ns": file_stat.st_mtime_ns,
                    "size": file_stat.st_size,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "saves": saves,
        "unsupported": unsupported,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(
            json.dumps(
                {"error": "usage: extract_save.py <path.sav> [--header-only] | --list <dir>"}
            )
        )
        return 2
    try:
        if argv[0] == "--list":
            if len(argv) < 2:
                json.dump({"error": "usage: --list <dir>"}, sys.stdout)
                return 2
            json.dump(list_dir(argv[1]), sys.stdout, separators=(",", ":"))
            return 0
        path = argv[0]
        if "--header-only" in argv:
            payload = {"schema_version": SCHEMA_VERSION, "header": header_info(path)}
        else:
            payload = extract_projection(path)
    except PARSE_ERROR as exc:
        json.dump({"error": "parse_error", "detail": str(exc), "path": path}, sys.stdout)
        return 1
    except Exception as exc:  # unexpected: surface the type, keep stdout valid JSON
        traceback.print_exc(file=sys.stderr)
        json.dump({"error": type(exc).__name__, "detail": str(exc), "path": path}, sys.stdout)
        return 1
    json.dump(payload, sys.stdout, separators=(",", ":"))
    return 0
