"""The request guard: every request must name this server as its Host, and a request that can
change a file must also come from this server's own page.

docs/frontend_vision.md §9.2 has the threat and the rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

__all__ = ["READS", "guard", "refusal"]

READS = frozenset({"GET", "HEAD", "OPTIONS"})

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _hosts(server: tuple[str, int] | None) -> set[str]:
    if not server:
        return set()
    host, port = server
    names = LOOPBACK if host in LOOPBACK else {host}
    spelled = {f"[{n}]" if ":" in n else n for n in names}
    out = {f"{n}:{port}" for n in spelled}
    if port == 80:
        out |= spelled
    return out


def _origin(headers: Mapping[str, str]) -> str | None:
    origin = headers.get("origin")
    if origin and origin != "null":
        return origin.rstrip("/")
    referer = urlsplit(headers.get("referer") or "")
    if referer.scheme and referer.netloc:
        return f"{referer.scheme}://{referer.netloc}"
    return None


def refusal(method: str, headers: Mapping[str, str], server: tuple[str, int] | None) -> str | None:
    """Why this request is refused, or ``None`` when it may go ahead."""
    host = (headers.get("host") or "").lower()
    allowed = _hosts(server)
    if host not in allowed:
        return (
            f"refused: Host {host or '(none)'!r} is not this server ({', '.join(sorted(allowed))})"
        )
    if method.upper() in READS:
        return None
    origin = _origin(headers)
    if origin != f"http://{host}":
        return (
            f"refused: a write must come from this page, http://{host}, not {origin or 'nowhere'}"
        )
    return None


async def guard(request: Request, call_next):
    said = refusal(request.method, request.headers, request.scope.get("server"))
    if said:
        return JSONResponse({"error": said}, status_code=403)
    return await call_next(request)
