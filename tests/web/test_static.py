"""The mount at ``/``: the built page when there is one, the instruction when there is not.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

The one file of this set that is about ``app.py`` rather than about a router. The static
mount is registered LAST -- a mount at ``/`` swallows every path that did not already
match -- so "the page is served from the root" is also the assertion that no router was
mounted after it. ``test_architecture.py`` covers the other half: that everything in
``static/`` is build output, and that none of it is tracked in git.

``static/`` is untracked build output now, so half of this file runs only where a build
has happened -- ``_built`` guards those tests -- and the other half is ABOUT the clone
that has no build: ``/`` must answer with the instruction to run one, with the JSON API
unaffected, because "the frontend is missing" is the normal first state of every clone
and not an error in the server.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.app import STATIC_DIR

_built = pytest.mark.skipif(
    not (STATIC_DIR / "index.html").is_file(),
    reason="static/ has not been built here (untracked output) -- run `npm run build`",
)

# --------------------------------------------------------------------- static


@_built
def test_the_static_bundle_ships_the_page_and_the_vendor_licence():
    """Redistributing Leaflet means shipping its BSD-2-Clause text next to it.

    Leaflet is compiled into ``app.js``, so there is no ``vendor/leaflet.js`` to point at --
    which is exactly why the licence text still has to be here, and why the bundle names the
    library in its own banner. The repository itself no longer redistributes Leaflet at all
    (the bundle is untracked), but every BUILD is a thing someone may pass on, so every
    build carries its notices: ``vite.config.ts`` copies the text out of
    ``node_modules/leaflet/LICENSE`` at build time. The obligation did not move when the
    packaging did -- twice now.
    """
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "app.css").is_file()
    licence = (STATIC_DIR / "vendor" / "LEAFLET-LICENSE").read_text(encoding="utf-8")
    assert "BSD 2-Clause License" in licence
    assert "Leaflet" in (STATIC_DIR / "app.js").read_text(encoding="utf-8")[:1000]


@_built
def test_the_page_is_served_from_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text


def test_a_clone_without_a_build_is_told_how_to_get_one(tmp_path, monkeypatch, state, game):
    """No ``static/`` means an instruction at ``/``, not a 404 -- and an untouched API.

    A fresh clone has no built frontend, by design, and the first thing its owner does is
    start the server and open the page. What they must see is the command that fixes it,
    not FastAPI's default ``{"detail": "Not Found"}`` -- and what must NOT change is the
    JSON surface, which does not depend on the build in any way. 503 and not 200, so that
    nothing scripted mistakes the apology for the page.

    A temp path via monkeypatch, not a real deletion: ``STATIC_DIR`` is read inside
    ``create_app``, so pointing the module attribute at an empty directory-to-be is the
    whole simulation.
    """
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import app as web_app

    monkeypatch.setattr(web_app, "STATIC_DIR", tmp_path / "static")
    instance = web_app.create_app(
        state_loader=lambda save=None, world=None: state,
        game_loader=lambda: game,
    )
    with TestClient(instance) as c:
        r = c.get("/")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("text/plain")
        assert "frontend not built" in r.text
        assert "npm ci && npm run build" in r.text
        assert "src/satisfactory_mcp/interfaces/web/frontend/" in r.text

        api = c.get("/api/summary")
        assert api.status_code == 200


def test_an_unrouted_api_path_answers_with_the_error_body(client):
    for path in ("/api/nope", "/api/plans/0000beef/nope"):
        r = client.get(path)
        assert r.status_code == 404
        assert r.json() == {"error": "nothing here"}
    wrong = client.delete("/api/summary", headers={"origin": "http://testserver"})
    assert wrong.status_code == 405 and wrong.json() == {"error": "method not allowed"}


@_built
def test_a_missing_page_file_keeps_the_default_404(client):
    r = client.get("/no-such-file.png")
    assert r.status_code == 404 and "error" not in r.json()
