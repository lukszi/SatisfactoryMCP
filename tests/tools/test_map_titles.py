"""The one name a map type is shown by, on the page and in chat (docs/maps_contract.md §3.5)."""

from __future__ import annotations

import time

import pytest

from satisfactory_mcp.core.gameassets.versions import RETIRED_STYLES, STYLES
from satisfactory_mcp.domain.maps import registry, titles


def october(day: int, hour: int, minute: int) -> float:
    """Epoch seconds of a local wall-clock time, so the words do not depend on the zone."""
    return time.mktime((2026, 10, day, hour, minute, 0, 0, 0, -1))


OCT5 = october(5, 15, 37)
OCT6 = october(6, 8, 14)
OCT6_LATER = october(6, 14, 5)


def axes(style: str, family: str = "render") -> dict:
    return {"style": {"id": style, "label": style}, "renderer": {"family": family}}


def row(ident: str, style: str, created: float | None = OCT6, **entry) -> dict:
    return {
        "id": ident,
        "status": entry.pop("status", "ready"),
        "axes": axes(style, entry.pop("family", "render")),
        "entry": {"label": None, "in_switcher": True, "created": created, **entry},
    }


@pytest.mark.parametrize(
    ("style", "family", "name"),
    [
        ("artwork", "artwork", "Game map"),
        ("satellite-painted", "render", "Satellite"),
        ("terrain-hypsometric", "render", "Terrain"),
        ("relief-night", "render", "Relief"),
        ("satellite-biome", "render", "Biome (old)"),
        ("relief-muted", "render", "Relief light (old)"),
    ],
)
def test_each_style_has_its_plain_name(style, family, name):
    assert titles.titles([row("a", style, family=family)], default=None) == {"a": name}


def test_no_two_styles_share_a_name_retired_ones_included():
    """A map drawn in a retired style is still listed, so its name must not be a drawn one's."""
    names = [style["name"] for style in (*STYLES.values(), *RETIRED_STYLES.values())]
    assert len(set(names)) == len(names), names


def test_an_artwork_or_an_unknown_style_is_named_from_what_it_says():
    legacy = {"style": {"id": "unknown", "label": "artwork"}, "renderer": {"family": "artwork"}}
    assert titles.style_name(legacy) == "Game map"
    assert titles.style_name({"style": {"id": "sepia-test", "label": "sepia"}}) == "Sepia"


def test_the_date_is_added_only_beside_another_of_the_same_style_in_the_switcher():
    rows = [
        row("painted-new", "satellite-painted", OCT6),
        row("painted-old", "satellite-painted", OCT5),
        row("relief", "relief-night", OCT6),
        row("biome", "satellite-biome", OCT6),
    ]
    assert titles.titles(rows, default=None) == {
        "painted-new": "Satellite · 6 Oct",
        "painted-old": "Satellite · 5 Oct",
        "relief": "Relief",
        "biome": "Biome (old)",
    }
    rows[1]["entry"]["in_switcher"] = False
    named = titles.titles(rows, default=None)
    assert named["painted-new"] == "Satellite", "the twin is not in the switcher"
    assert named["painted-old"] == "Satellite · 5 Oct", "the Maps tab still tells it apart"
    rows[1]["entry"]["in_switcher"] = True
    rows[1]["status"] = "failed"
    assert titles.titles(rows, default=None)["painted-new"] == "Satellite"


def test_two_of_one_style_built_on_one_day_add_the_time():
    rows = [
        row("terrain-a", "terrain-hypsometric", OCT6),
        row("terrain-b", "terrain-hypsometric", OCT6_LATER),
        row("terrain-c", "terrain-hypsometric", OCT5),
    ]
    assert titles.titles(rows, default=None) == {
        "terrain-a": "Terrain · 6 Oct 08:14",
        "terrain-b": "Terrain · 6 Oct 14:05",
        "terrain-c": "Terrain · 5 Oct",
    }


def test_the_default_carries_a_star_after_its_name():
    rows = [row("map", "artwork", family="artwork"), row("sat", "satellite-painted")]
    assert titles.titles(rows, default="map") == {"map": "Game map ★", "sat": "Satellite"}
    rows.append(row("sat-old", "satellite-painted", OCT5, in_switcher=False))
    named = titles.titles(rows, default="sat-old")
    assert named["sat-old"] == "Satellite · 5 Oct ★", "the default shows, ticked or not"
    assert named["sat"] == "Satellite · 6 Oct"


def test_a_label_wins_over_the_generated_name():
    rows = [
        row("painted-new", "satellite-painted", OCT6, label="River test"),
        row("painted-old", "satellite-painted", OCT5),
    ]
    assert titles.titles(rows, default="painted-new") == {
        "painted-new": "River test ★",
        "painted-old": "Satellite",
    }


def test_a_time_the_sidecar_does_not_give_adds_nothing():
    rows = [row("a", "terrain-hypsometric", None), row("b", "terrain-hypsometric", OCT5)]
    assert titles.titles(rows, default=None) == {"a": "Terrain", "b": "Terrain · 5 Oct"}
    assert titles.date_word(OCT6_LATER, with_time=True) == "6 Oct 14:05"


def test_the_view_and_chat_give_every_type_the_same_title(local):
    from satisfactory_mcp.interfaces.mcp.tools import settings as settings_tool

    rows = {row["id"]: row for row in registry.view()["types"]}
    assert rows["map"]["title"] == "Game map ★"
    assert rows["terrain-r4-502094"]["title"].startswith("Terrain · ")
    line = next(r for r in settings_tool.settings().splitlines() if r.startswith("# base maps"))
    assert 'map "Game map ★" (default)' in line
    assert f'terrain-r4-502094 "{rows["terrain-r4-502094"]["title"]}"' in line
