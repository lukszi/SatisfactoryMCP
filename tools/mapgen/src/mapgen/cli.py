"""``python -m mapgen <command> [args]``: one entry point for every map generator.

Standard library only at module scope. A spawned worker re-imports its parent's
``__main__``, and a heavy entry costs every worker its own numpy (1516 MB against 12 MB
private, measured); the command's module is imported only once it is chosen.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence

#: command -> (module holding ``main()``, arguments put in front of the caller's).
COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "heightmap": ("mapgen.heightmap", ()),
    "caves": ("mapgen.heightmap", ("--caves",)),
    "rocks": ("mapgen.heightmap", ("--rocks",)),
    "renders": ("mapgen.pipeline", ()),
    "artwork": ("mapgen.artwork", ()),
    "paint": ("mapgen.gamedata.paint", ()),
    "check-fill": ("mapgen.check_fill", ()),
}


def usage() -> str:
    return "usage: python -m mapgen {" + ",".join(COMMANDS) + "} [args...]"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command's ``main()`` with ``sys.argv`` set as if it were the script.

    ``sys.argv[0]`` is left alone, so a thin ``tools/gen_*.py`` that calls this keeps
    its own name in every message that quotes it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(usage())
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n{usage()}")
        return 2
    module_name, prefix = COMMANDS[command]
    sys.argv = [sys.argv[0] if sys.argv else "mapgen", *prefix, *rest]
    module = importlib.import_module(module_name)
    return module.main()
