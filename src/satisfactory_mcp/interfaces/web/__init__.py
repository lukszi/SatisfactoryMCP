"""A JSON + map interface over the same domain services the MCP tools call.

Importing this package must not require ``fastapi`` or ``uvicorn``: they live in the
optional ``web`` extra, so only ``app``, ``routers``, ``serial``, the ``watch`` package and
``__main__`` touch the ASGI stack, and nothing outside ``interfaces/`` may import any of them.
This package never imports ``interfaces.mcp`` either: the two adapters share the domain, not
each other. ``tests/architecture/`` checks all of it.
"""
