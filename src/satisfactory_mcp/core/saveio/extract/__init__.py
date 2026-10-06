"""The save extractor, run as a SEPARATE PROCESS from the server.

    python -m satisfactory_mcp.core.saveio.extract <path.sav> [--header-only] | --list <dir>

A subprocess so that an unreadable or torn save cannot take the server down, and so that the
projection is small enough to be the test fixture. The parser answers "what does this file
say"; this package answers "what does the server need", and ``parser`` is the only module in
the application that imports ``pioneersav``. See ``docs/save-projection.md``.
"""

from __future__ import annotations

from ..projection import SCHEMA_VERSION
from .cli import list_dir, main
from .parser import header_info
from .walk import extract_projection

__all__ = ["SCHEMA_VERSION", "extract_projection", "header_info", "list_dir", "main"]
