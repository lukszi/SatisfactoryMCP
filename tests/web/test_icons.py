"""``/api/icons/{desc}``: one item's picture, and the four ways it can be absent.

Every test here points the router's directory at a ``tmp_path`` holding a handful of
one-pixel PNGs, so nothing reads the reader's own ``data/local/`` and nothing needs the game
installed. The router reads that directory at call time precisely so this is possible.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.interfaces.web.routers.assets import icons as web_icons

#: The smallest thing Pillow and every browser agree is a PNG: 1x1, fully transparent.
#: Its CONTENT is irrelevant here -- what is under test is which bytes are served and with
#: which headers, not anybody's decoder -- so it is written literally rather than generated,
#: and this file therefore needs neither Pillow nor the ``gen`` extra.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005a3f2e5480000000049454e44ae426082"
)


@pytest.fixture
def icons_dir(tmp_path, monkeypatch):
    """A generated icons directory with two classes in it, and a manifest beside them."""
    directory = tmp_path / "local" / "icons"
    directory.mkdir(parents=True)
    for name in ("Desc_IronPlate_C", "Build_StorageContainerMk1_C"):
        (directory / f"{name}.png").write_bytes(_PNG)
    (directory / web_icons.ICONS_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "_meta": {
                    "source": {"game_version_pinned": "buildVersion 495413"},
                    "counts": {"icons_written": 2, "bytes": 2 * len(_PNG), "written_px": 256},
                },
                "icons": {},
                "unresolved": {"Desc_PillarTop_C": "the dump names no icon (None)"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_icons, "_icons_dir", lambda: directory)
    return directory


@pytest.fixture
def empty_icons_dir(tmp_path, monkeypatch):
    """The state a clone is in: nobody has run the generator, so there is no directory."""
    monkeypatch.setattr(web_icons, "_icons_dir", lambda: tmp_path / "local" / "icons")
    return tmp_path / "local" / "icons"


def test_an_icon_is_served_as_a_png_with_the_build_tag_the_client_busts_the_cache_with(
    client, icons_dir
):
    """The bytes, the type, and the tag -- which is the header a client actually needs.

    ``X-Icons-Build`` is a digest of what the generator recorded, so it changes when the
    directory is regenerated and at no other time. That is what makes ``?v=`` meaningful,
    and therefore what makes ``immutable`` safe below.
    """
    r = client.get("/api/icons/Desc_IronPlate_C")
    assert r.status_code == 200
    assert r.content == _PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-icons-build"] and len(r.headers["x-icons-build"]) == 12
    assert r.headers["etag"] == f'"{r.headers["x-icons-build"]}"'


def test_a_versioned_url_is_immutable_and_a_bare_one_revalidates(client, icons_dir):
    """The tile route's rule, and it is the untagged half that matters.

    ``immutable`` is EARNED by the ``?v=`` tag and only by it: a tagged URL changes whenever
    the directory is recut, so the response behind it never can. Caching an untagged answer
    hard is how a regenerated directory stays invisible behind a year-old probe -- so those
    revalidate, and the ETag makes that a 304 rather than the bytes again.
    """
    tag = client.get("/api/icons/Desc_IronPlate_C").headers["x-icons-build"]

    fresh = client.get(f"/api/icons/Desc_IronPlate_C?v={tag}")
    assert fresh.headers["cache-control"] == "public, max-age=31536000, immutable"

    bare = client.get("/api/icons/Desc_IronPlate_C")
    assert bare.headers["cache-control"] == "no-cache"

    revalidated = client.get("/api/icons/Desc_IronPlate_C", headers={"if-none-match": f'"{tag}"'})
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == f'"{tag}"'


def test_the_build_tag_moves_when_the_directory_is_regenerated_and_not_otherwise(client, icons_dir):
    """A cache tag that did not move on a recut would pin every client to stale artwork.

    Both directions: the tag is stable across requests against one directory, and rewriting
    the manifest's provenance -- which is what a regeneration does -- changes it. Asserted
    against the manifest rather than against a file's mtime, because the manifest is what
    the generator writes and mtimes are what a copy destroys.
    """
    first = client.get("/api/icons/Desc_IronPlate_C").headers["x-icons-build"]
    assert client.get("/api/icons/Desc_IronPlate_C").headers["x-icons-build"] == first

    manifest = icons_dir / web_icons.ICONS_MANIFEST_NAME
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["_meta"]["source"]["game_version_pinned"] = "buildVersion 500000"
    manifest.write_text(json.dumps(body), encoding="utf-8")

    assert client.get("/api/icons/Desc_IronPlate_C").headers["x-icons-build"] != first


def test_a_probe_for_an_absent_icon_is_204_and_the_get_says_which_tool_writes_them(
    client, empty_icons_dir
):
    """The map image's rule: an absent optional file is expected, so HEAD must not go red.

    A 404 logged on every clean page load trains the reader to ignore console errors on this
    page. The GET keeps its 404 and names the generator, because anything actually fetching
    bytes deserves the reason -- and the reason is a command, not an apology.
    """
    assert client.head("/api/icons/Desc_IronPlate_C").status_code == 204

    missing = client.get("/api/icons/Desc_IronPlate_C")
    assert missing.status_code == 404
    assert "gen_item_icons.py" in missing.json()["error"]
    assert "never committed" in missing.json()["error"]


def test_a_generated_directory_without_this_class_gives_a_different_answer(client, icons_dir):
    """Two absences, two sentences, because they are two different facts.

    "Nobody has run the generator" is fixed by running it. "This class has no picture in the
    container" is not fixed by anything -- 6 of the game's 750 item classes are in that
    state -- so telling a reader to run the tool again would send them round a loop.
    """
    missing = client.get("/api/icons/Desc_PillarTop_C")
    assert missing.status_code == 404
    error = missing.json()["error"]
    assert "gen_item_icons.py" not in error, "running it again would not help"
    assert "unresolved" in error, "the manifest says why, per class"


@pytest.mark.parametrize(
    "segment",
    [
        "..",
        "%2e%2e",
        "Desc_IronPlate_C.png",
        "sub%2fDesc_IronPlate_C",
        "Desc-IronPlate-C",
        "Desc_IronPlate_C%00",
        "a" * 129,
    ],
)
def test_a_segment_that_is_not_a_class_name_never_becomes_a_path(client, icons_dir, segment):
    """Validated, not repaired -- so there is no join for a traversal to escape through.

    A descriptor class is ``[A-Za-z0-9_]`` and nothing else, so every one of these is refused
    before a ``Path`` exists. That is the difference between this and a sanitiser: there is
    no list of things to strip, and therefore no encoding that gets past the list.

    ``Desc_IronPlate_C.png`` is in here on purpose and is the friendly-looking one: the file
    of that exact name IS on disk, and it still 404s, because the route names classes rather
    than files and a reader who guesses the extension must not be quietly right.
    """
    r = client.get(f"/api/icons/{segment}")
    assert r.status_code == 404
    assert r.content != _PNG, "a refused segment must never reach a file"


def test_the_router_reads_the_directory_the_generator_writes(icons_dir):
    """The names the tool and the endpoint have to agree about, and nothing else joins them.

    Imported from the tool rather than retyped on this side, the way ``routers/assets/tiles.py``
    takes the pyramid's names from the cutter: a second copy is a second opinion waiting to
    happen, and the cost of getting it wrong is an endpoint that 404s over a directory full
    of pictures.
    """
    gen = pytest.importorskip("tools.gen_item_icons")

    assert gen.ICONS_DIR_NAME == web_icons.ICONS_DIR_NAME
    assert gen.MANIFEST_NAME == web_icons.ICONS_MANIFEST_NAME
    assert gen.BUILD_PIN_PATH == ("_meta", "source", "game_version_pinned")
    # And the file name the tool writes is the one `icon_path` looks for, spelled by the tool.
    assert web_icons.icon_path("Desc_IronPlate_C").name == "Desc_IronPlate_C.png"
