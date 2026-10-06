"""``satisfactory-mcp-web`` -- serve the JSON API and the map on localhost.

Bound to 127.0.0.1: the API serves the contents of the player's save directory and applies
no authentication at all, so reaching it from the network must stay a deliberate act.
Uvicorn is handed the import string rather than the object, which is what ``--reload`` and
the worker model need.

The host and the default port live in ``config`` (``SATISFACTORY_WEB_PORT`` overrides the
port), because the MCP tools build links to this page from the same two values.
"""

from __future__ import annotations

import argparse

import uvicorn

from ... import config

__all__ = ["main"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="satisfactory-mcp-web")
    parser.add_argument("--port", type=int, default=config.web_port())
    port = parser.parse_args(argv).port
    uvicorn.run("satisfactory_mcp.interfaces.web.app:app", host=config.WEB_HOST, port=port)


if __name__ == "__main__":
    main()
