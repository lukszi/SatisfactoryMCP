"""``/api/regions``: the biome raster the base map is drawn from.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py`` --
so nothing in this file spawns the sidecar or reads a ``.sav``. The raster itself is the
world's own geography and needs neither: it is ``data/region_names.json``, which is
committed, and ``tests/data/test_regions_provenance.py`` is what checks it against the game.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")


def test_regions_serve_the_biome_raster_in_metres(client):
    """The base map's only source: 30 rows of 30 cells, a legend, and the frame."""
    r = client.get("/api/regions")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"], "geography does not change per save"
    body = r.json()
    assert len(body["grid"]) == 30
    assert {len(row) for row in body["grid"]} == {30}
    assert body["cell_m"] == 256.0
    assert (body["x0_m"], body["y0_m"]) == (-3360.0, -3800.0)
    assert body["legend"]["E"] == "Dune Desert"
    # Every letter drawn has a name, or the page paints an unlabelled colour.
    drawn = {ch for row in body["grid"] for ch in row} - {"."}
    assert drawn <= set(body["legend"])
    entry = body["regions"]["Dune Desert"]
    assert len(entry["centroid_m"]) == 2
    assert len(entry["bbox_m"]) == 4
    # Metres, one decimal, like every other coordinate on this surface.
    assert abs(entry["centroid_m"][0]) < 5000


def test_every_region_cell_lands_inside_that_regions_own_bbox(client):
    """The orientation guard, and the reason this endpoint reports x0/y0 at all.

    Row 0 is the NORTHERN edge because ``y0_m`` is the smallest y and game +Y is south.
    Get that backwards and the map still draws -- mirrored -- so it is checked against
    the per-region bounding boxes in the same file rather than by eye: reconstruct each
    cell's extent from the frame and assert it is inside the region it claims to be.
    """
    body = client.get("/api/regions").json()
    cell = body["cell_m"]
    counted = 0
    for j, row in enumerate(body["grid"]):
        for i, letter in enumerate(row):
            if letter == ".":
                continue
            box = body["regions"][body["legend"][letter]]["bbox_m"]
            x, y = body["x0_m"] + i * cell, body["y0_m"] + j * cell
            assert box[0] <= x and x + cell <= box[2], (i, j, letter)
            assert box[1] <= y and y + cell <= box[3], (i, j, letter)
            counted += 1
    assert counted > 400, "a base map of a few dozen cells is not a base map"
    # And the desert is in the north-east, which is the one fact a mirrored map fails.
    #
    # Stated as "where E is" rather than "what is in the corner". The corner itself is the
    # game's No Man's Land -- outer coast, which the retired wiki trace painted as desert
    # right to the edge and the game does not -- so a corner test now asserts the coastline
    # rather than the orientation. Presence in one quadrant and absence in the opposite one
    # is what a mirror or a transpose actually breaks.
    desert = [
        (i, j)
        for j, row in enumerate(body["grid"])
        for i, letter in enumerate(row)
        if body["legend"].get(letter) == "Dune Desert"
    ]
    assert desert, "Dune Desert is not on the map at all"
    assert all(i > 15 for i, _j in desert), "Dune Desert has ground in the western half"
    assert all(j < 20 for _i, j in desert), "Dune Desert has ground in the southern quarter"


def test_region_label_anchors_land_on_their_own_regions_ground(client):
    """A centroid can fall in a neighbour's cell (Titan Forest's lands in the Swamp).
    The label anchor may not: printed names and the right-click inspector must agree."""
    body = client.get("/api/regions").json()
    cell = body["cell_m"]
    letters = {name: ch for ch, name in body["legend"].items()}
    for name, entry in body["regions"].items():
        x, y = entry["label_m"]
        i = int((x - body["x0_m"]) // cell)
        j = int((y - body["y0_m"]) // cell)
        assert body["grid"][j][i] == letters[name], (name, entry["label_m"])
