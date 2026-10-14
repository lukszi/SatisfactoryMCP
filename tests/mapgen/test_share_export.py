"""``python -m mapgen share-export``: the public map-only site, and the scan that guards it.

docs/map/sharing.md. The layer here is the lit terrain of tests/web/assets/test_light_tiles.py,
drawn apart at its trees, with a sidecar that also carries what must never leave the machine.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

from mapgen import cli
from mapgen.commands import share_export
from mapgen.share import manifest, privacy
from satisfactory_mcp import config

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
)
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"
PAGE_HTML = (
    '<!doctype html><html><head><script type="module" crossorigin src="./assets/app-a1.js">'
    '</script><link rel="stylesheet" href="./assets/app-b2.css"></head>'
    '<body><div id="map"></div></body></html>'
)


def _tree(root: Path, max_z: int, suffix: str, payload: bytes) -> None:
    for z in range(max_z + 1):
        for x in range(1 << z):
            for y in range(1 << z):
                path = root / str(z) / f"{x}_{y}{suffix}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)


def _layer(local: Path, digest: str = "sha256:x") -> None:
    """A lit terrain apart at its trees (z0 and one z1 tile), its sidecars full of local detail."""
    layer = local / "renders" / "terrain"
    _tree(layer / "tiles", 2, ".png", PNG)
    _tree(layer / "unlit", 2, ".webp", WEBP)
    for name in ("0/0_0", "1/1_0"):
        (layer / "trees" / name).parent.mkdir(parents=True, exist_ok=True)
        (layer / "trees" / f"{name}.webp").write_bytes(WEBP)
    sidecar = {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": "terrain",
            "machine": "C:\\Users\\someone\\render",
            "tiles": {"max_z": 2, "count": 21},
            "light": {
                "dir": "../light",
                "unlit_dir": "unlit",
                "unlit_tiles": {"max_z": 2, "count": 21, "bytes": 5, "layout": "unlit/{z}/{x}_{y}.webp"},
                "trees_dir": "trees",
                "trees_tiles": {"max_z": 1, "count": 2, "bytes": 7, "sparse": True},
                "params": {"space": "srgb", "ambient": 0.5, "sky": [1, 1, 1], "sun": [1, 1, 1],
                           "tone_knee": 1.0, "tone_white": 1.0, "cache": "/home/someone/x"},
                "baked_sun": [225.0, 62.25],
            },
        }
    }  # fmt: skip
    (layer / "meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    light = local / "renders" / "light"
    for suffix in (".nrm.webp", ".hz.webp"):
        _tree(light / "tiles", 2, suffix, WEBP)
    meta = {"_meta": {"tiles": {"max_z": 2, "count": 42, "bytes": 99},
                      "light": {"id": "sun", "dirs": 32, "digest": digest, "normalise_min_el": 35.0,
                                "hz_encoding": "written by someone@example.org"}}}  # fmt: skip
    (light / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The data folder, the built page, and where the export goes."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    _layer(tmp_path / "data" / "local")
    page = tmp_path / "page"
    (page / "assets").mkdir(parents=True)
    (page / "share.html").write_text(PAGE_HTML, encoding="utf-8")
    (page / "assets" / "app-a1.js").write_text("console.log(1)", encoding="utf-8")
    (page / "assets" / "app-b2.css").write_text("#map{inset:0}", encoding="utf-8")
    monkeypatch.setattr(privacy, "private_words", lambda extra=(): ["hostname-of-test"])
    return tmp_path


def _export(site: Path, *extra: str) -> int:
    argv = [str(site / "out"), "--ids", "terrain", "--page", str(site / "page"), *extra]
    return share_export.main(argv)


def _files(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}


def test_the_export_is_plain_files_at_build_tagged_paths(site):
    assert _export(site) == 0
    out = site / "out"
    found = _files(out)
    sitejson = json.loads((out / "data" / "site.json").read_text(encoding="utf-8"))
    row = sitejson["layers"]["terrain"]
    assert row["dir"].startswith("t/terrain-") and row["light"].startswith("t/light-")
    assert f"{row['dir']}/tiles/2/3_3.png" in found
    assert f"{row['dir']}/unlit/2/3_3.webp" in found
    assert f"{row['light']}/2/3_3.hz.webp" in found and f"{row['light']}/0/0_0.nrm.webp" in found
    assert {f"{row['dir']}/trees/0/0_0.webp", f"{row['dir']}/trees/1/1_0.webp"} <= found
    assert {"index.html", "data/maps.json", "assets/app-a1.js", "assets/app-b2.css"} <= found
    assert not [name for name in found if name.endswith(".part")]
    assert row["suffix"] == {"unlit": ".webp", "trees": ".webp"}

    bits = base64.b64decode(row["trees"])
    present = {i for i in range(len(bits) * 8) if bits[i >> 3] >> (i & 7) & 1}
    assert present == {0, 1 + 0 * 2 + 1}, "z0, and z1 x=1 y=0: what the page's hasTrees reads"

    maps = json.loads((out / "data" / "maps.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in maps["types"]] == ["terrain"] and maps["default"] == "terrain"


def test_nothing_private_from_the_sidecars_reaches_the_site(site):
    assert _export(site) == 0
    out = site / "out"
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.json"))
    for leak in ("someone", "machine", "cache", "digest", "hz_encoding", "sha256", "generator"):
        assert leak not in text, leak
    row = json.loads((out / "data" / "site.json").read_text(encoding="utf-8"))["layers"]["terrain"]
    light = json.loads(row["headers"]["X-Map-Light"])
    assert manifest.validate(light, manifest.LIGHT_SCHEMA) == []
    assert light["model"] == {"dirs": 32, "normalise_min_el": 35.0}
    assert light["parts"] == {"trees": {"max_z": 1, "sparse": True}}


def test_max_z_caps_the_files_and_every_depth_the_page_is_told(site):
    assert _export(site, "--max-z", "1", "--parts", "lit") == 0
    out = site / "out"
    found = _files(out)
    assert not [n for n in found if "/2/" in n], "nothing deeper than z1"
    assert not [n for n in found if "/tiles/" in n], "no baked tiles without --parts baked"
    headers = json.loads((out / "data" / "site.json").read_text(encoding="utf-8"))["layers"][
        "terrain"
    ]["headers"]
    assert headers["X-Map-Tile-Max-Z"] == "1"
    light = json.loads(headers["X-Map-Light"])
    assert light["max_z"] == light["unlit_max_z"] == 1


def test_a_rerun_copies_nothing_and_a_new_render_lands_beside_the_old_until_pruned(site, capsys):
    assert _export(site) == 0
    first = json.loads((site / "out" / "data" / "site.json").read_text(encoding="utf-8"))
    assert _export(site) == 0
    assert "(0 copied, 0 linked this run)" in capsys.readouterr().out
    meta = site / "data" / "local" / "renders" / "light" / "meta.json"
    meta.write_text(meta.read_text(encoding="utf-8").replace('"bytes": 99', '"bytes": 100'))
    assert _export(site) == 0
    second = json.loads((site / "out" / "data" / "site.json").read_text(encoding="utf-8"))
    old, new = first["layers"]["terrain"]["light"], second["layers"]["terrain"]["light"]
    assert old != new and (site / "out" / old).is_dir(), "the live site keeps the old folder"
    assert second["render"] != first["render"]
    assert _export(site, "--prune") == 0
    assert not (site / "out" / old).exists() and (site / "out" / new).is_dir()


def _tile_pairs(site: Path) -> list[tuple[Path, Path]]:
    """(render file, export file) for the baked tiles."""
    row = json.loads((site / "out" / "data" / "site.json").read_text(encoding="utf-8"))
    source = site / "data" / "local" / "renders" / "terrain" / "tiles"
    target = site / "out" / row["layers"]["terrain"]["dir"] / "tiles"
    return [(p, target / p.relative_to(source)) for p in source.rglob("*.png")]


def test_tiles_are_hard_links_and_written_files_are_not(site, capsys):
    assert _export(site) == 0
    assert "(0 copied, 86 linked this run)" in capsys.readouterr().out
    pairs = _tile_pairs(site)
    assert pairs and all(t.samefile(s) for s, t in pairs)
    out = site / "out"
    for rel in ("index.html", "data/maps.json", "data/site.json", "assets/app-a1.js"):
        assert (out / rel).stat().st_nlink == 1, rel


def test_a_failed_link_falls_back_to_a_copy(site, monkeypatch, capsys):
    def refuse(*_args: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    assert _export(site) == 0
    assert " 0 linked this run" in capsys.readouterr().out
    pairs = _tile_pairs(site)
    assert pairs and not any(t.samefile(s) for s, t in pairs)
    assert all(t.read_bytes() == s.read_bytes() for s, t in pairs)
    assert not [n for n in _files(site / "out") if n.endswith(".part")]


def test_copy_never_links(site, monkeypatch):
    def forbidden(*_args: object) -> None:
        raise AssertionError("linked under --copy")

    monkeypatch.setattr(os, "link", forbidden)
    assert _export(site, "--copy") == 0
    assert all(t.stat().st_nlink == 1 for _s, t in _tile_pairs(site))


def test_the_dry_run_counts_and_writes_nothing(site, capsys):
    assert _export(site, "--dry-run") == 0
    assert not (site / "out").exists()
    assert "would hold" in capsys.readouterr().out


def test_a_folder_that_is_not_an_export_is_refused(site, capsys):
    (site / "out").mkdir()
    (site / "out" / "notes.docx").write_text("x")
    assert _export(site) == 1
    assert "not an export" in capsys.readouterr().out


def _png_with_text() -> bytes:
    body = b"Comment\x00made on a laptop"
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body
    chunk += struct.pack(">I", zlib.crc32(b"tEXt" + body))
    return PNG[:33] + chunk + PNG[33:]


@pytest.mark.parametrize(
    ("rel", "content", "why"),
    [
        ("assets/app-a1.js", 'var p="C:\\\\Users\\\\someone\\\\repo"', "Windows home path"),
        ("assets/app-a1.js", 'var p="/home/someone/repo"', "Unix home path"),
        ("assets/app-a1.js", 'var m="someone@example.org"', "email address"),
        ("assets/app-a1.js", "var b='hostname-of-test'", "host or author name"),
        ("assets/app-a1.js", "var h='" + "ab12" * 10 + "'", "git or SHA-1 hash"),
        ("assets/app-a1.js", "eval('1')", "code built from a string"),
        ("assets/app-a1.js", "//# sourceMappingURL=app.js.map", "source map"),
        ("assets/app-b2.css", '.x{background:url("file:///C:/x.png")}', "file URL"),
        ("assets/.htaccess", "Options +Indexes", "file type the site does not serve"),
        ("assets/app.js.map", "{}", "file type the site does not serve"),
    ],
)
def test_the_scan_refuses_what_a_public_site_must_not_hold(site, capsys, rel, content, why):
    target = site / "page" / rel
    target.write_text(content, encoding="utf-8")
    assert _export(site) == 1
    printed = capsys.readouterr().out
    assert "NOT exported" in printed and why in printed
    assert not (site / "out" / "data" / "site.json").exists(), "manifests come last"


@pytest.mark.parametrize(
    ("html", "why"),
    [
        ('<script>alert(1)</script><div id="map"></div>', "inline script"),
        ("<style>body{}</style>", "inline style block"),
        ('<div style="color:red"></div>', "style attribute"),
        ('<img src="x.png" onerror="go()">', "inline event handler"),
        ('<script src="https://cdn.example.net/x.js"></script>', "file from another origin"),
    ],
)
def test_the_page_must_work_under_a_strict_content_security_policy(site, capsys, html, why):
    (site / "page" / "share.html").write_text(PAGE_HTML + html, encoding="utf-8")
    assert _export(site) == 1
    assert why in capsys.readouterr().out


def test_an_image_carrying_metadata_is_never_copied(site, capsys):
    tile = site / "data" / "local" / "renders" / "terrain" / "tiles" / "1" / "0_1.png"
    tile.write_bytes(_png_with_text())
    assert privacy.image_problem("0_1.png", _png_with_text()) == "a tEXt chunk"
    with pytest.raises(ValueError, match="tEXt"):
        _export(site)


def test_a_manifest_key_off_the_allowlist_is_named():
    doc = {"version": 1, "default": "x", "types": [{"id": "x", "dir": "renders/x"}]}
    assert manifest.validate(doc, manifest.MAPS_SCHEMA) == [
        "/types[0]/dir: key not on the allowlist"
    ]
    site_doc = {"layers": {"../escape": {"dir": "t/x"}}}
    assert manifest.validate(site_doc, manifest.SITE_SCHEMA) == [
        "/layers/../escape: key not on the allowlist"
    ]


def test_the_command_is_reachable_through_the_entry_point(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mapgen"])
    with pytest.raises(SystemExit) as stopped:
        cli.main(["share-export", "--help"])
    assert stopped.value.code == 0
    assert "--max-z" in capsys.readouterr().out
