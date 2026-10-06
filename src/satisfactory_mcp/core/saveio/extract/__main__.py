"""``python -m satisfactory_mcp.core.saveio.extract <path.sav> [--header-only] | --list <dir>``"""

import sys

from .cli import main

raise SystemExit(main(sys.argv[1:]))
