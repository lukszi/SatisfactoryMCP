"""``satisfactory-mcp-web`` -- serve the JSON API and the map on localhost.

Bound to 127.0.0.1: the API serves the contents of the player's save directory and applies
no authentication at all, so reaching it from the network must stay a deliberate act.
Uvicorn is handed the import string rather than the object, which is what ``--reload`` and
the worker model need.
"""

from __future__ import annotations

import argparse

import uvicorn

__all__ = ["HOST", "PORT", "main"]

HOST = "127.0.0.1"
#: Arbitrary and high, chosen to collide with nothing: 8712 is free in the IANA list.
PORT = 8712


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="satisfactory-mcp-web")
    parser.add_argument("--port", type=int, default=PORT)
    port = parser.parse_args(argv).port
    uvicorn.run("satisfactory_mcp.interfaces.web.app:app", host=HOST, port=port)


if __name__ == "__main__":
    main()
