"""Deep links into satisfactory-calculator.com's interactive map.

The fragment format comes from one working link the player supplied; the layer tokens were
read from the page itself. WebFetch gets 403 from this host, but curl from the user's own
machine returns it. That distinction mattered: inferring the tokens from the single oil
example got two of them wrong.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import maplink

pytestmark = pytest.mark.integration

#: The link the player supplied, verbatim.
SAMPLE = (
    "https://satisfactory-calculator.com/en/interactive-map"
    "#4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;"
    "oilImpure;oilWellImpure;oilPure"
)


def test_the_generated_shape_matches_the_supplied_link():
    """Same zoom, same coordinate, same group, same six oil tokens. Order differs and
    that is fine -- the site takes a set."""
    url = maplink.calculator_map_url(40351, -208857, maplink.layers_for(["Desc_LiquidOil_C"]))
    head, _, fragment = url.partition("#")
    zoom, x, y = fragment.split("|")[0].split(";")
    assert head == maplink.CALCULATOR_MAP_URL
    assert (zoom, x, y) == ("4.75", "40351", "-208857")
    assert fragment.split("|")[1] == "gameLayer"

    sample_tokens = set(SAMPLE.split("|")[-1].split(";"))
    assert set(fragment.split("|")[2].split(";")) == sample_tokens


def test_coordinates_are_save_centimetres_not_metres():
    """Every other tool in this MCP quotes metres. Doing the conversion anywhere but the
    caller would put a metre value 1/100th of the way across the map, near the origin,
    which looks plausible and is wrong."""
    url = maplink.calculator_map_url(-106900, -127300)
    assert "#4.75;-106900;-127300|" in url


def test_a_coordinate_is_rounded_not_truncated():
    url = maplink.calculator_map_url(-109969.6, -124184.4)
    assert ";-109970;-124184|" in url


def test_both_node_and_well_variants_are_offered():
    """Crude Oil exists as nodes AND resource wells; a link showing one and hiding the
    other answers a narrower question than was asked."""
    tokens = maplink.layers_for(["Desc_LiquidOil_C"])
    assert "oilPure" in tokens and "oilWellPure" in tokens
    assert len(tokens) == 6, tokens


def test_every_purity_is_included():
    """Opening the map on one impure node while hiding the pure one beside it is worse
    than showing all three."""
    tokens = maplink.layers_for(["Desc_OreIron_C"])
    assert {"ironImpure", "ironNormal", "ironPure"} <= set(tokens)


def test_geysers_do_take_purities_on_this_map():
    """Our node table gives geysers no purity, so a bare "geyser" token was guessed. The
    page has geyserImpure/Normal/Pure -- a guess that would have opened the map correctly
    with the overlay silently missing, which is the failure mode hardest to notice."""
    assert maplink.layers_for(["Desc_Geyser_C"]) == [
        "geyserImpure",
        "geyserNormal",
        "geyserPure",
    ]


def test_nitrogen_carries_the_gas_in_its_stem():
    """Inferring from oil gave nitrogenWell*; the page says nitrogenGasWell*. No amount
    of pattern-matching on "oil" would have produced it."""
    tokens = maplink.layers_for(["Desc_NitrogenGas_C"])
    assert tokens == [
        "nitrogenGasWellImpure",
        "nitrogenGasWellNormal",
        "nitrogenGasWellPure",
    ]


def test_a_well_only_resource_has_no_bare_node_token():
    """Nitrogen and water exist only as wells, so nitrogenGasImpure is not a layer."""
    for cls in ("Desc_NitrogenGas_C", "Desc_Water_C"):
        assert all("Well" in tok for tok in maplink.layers_for([cls])), cls


def test_a_node_only_resource_has_no_well_token():
    """Coal has no wells; asking for them anyway must not invent coalWellPure."""
    assert maplink.layers_for(["Desc_Coal_C"], ["node", "well"]) == [
        "coalImpure",
        "coalNormal",
        "coalPure",
    ]


def test_collectible_tokens_are_the_ones_the_page_uses():
    assert set(maplink.COLLECTIBLES.values()) == {
        "greenSlugs",
        "yellowSlugs",
        "purpleSlugs",
        "hardDrives",
        "mercerSpheres",
        "somersloops",
    }


def test_an_unknown_resource_contributes_no_token_rather_than_a_guess():
    assert maplink.layers_for(["Desc_NotAThing_C"]) == []


def test_the_fragment_delimiters_survive_escaping():
    """; and | are structural here; percent-encoding them would break the fragment."""
    url = maplink.calculator_map_url(1, 2, ["oilPure", "ironNormal"])
    assert "|gameLayer|oilPure;ironNormal" in url
    assert "%3B" not in url and "%7C" not in url


def test_every_target_gets_this_projects_own_map_link(game):
    """The README's flagship example -- "show the coal powerplant on the map" -- opened
    satisfactory-calculator.com, which cannot draw the player's world at all. Only a
    sited plan got a link to the map that can, though local_map_url was right there."""
    from satisfactory_mcp import server as srv

    for target in ("0,0", "resource:Crude Oil"):
        out = srv.show_on_map(target)
        assert "local map: " + maplink.local_base() + "#" in out, target
        assert "public map: " + maplink.CALCULATOR_MAP_URL + "#" in out, target
        # The local one leads, because it is the one that knows what was built.
        assert out.index("local map:") < out.index("public map:")


def test_well_variants_are_offered_only_where_wells_exist(game):
    """Coal is node-only. Emitting coalWellPure invents a token for something that does
    not exist -- harmless, since an unknown layer is ignored, but it is a guess with no
    need to be made: the node table already knows which resources have wells."""
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()

    def kinds_of(res):
        return sorted(
            {"well" if r["kind"].startswith("well") else "node" for r in table.by_resource(res)}
        )

    assert kinds_of("Desc_Coal_C") == ["node"]
    assert kinds_of("Desc_NitrogenGas_C") == ["well"]
    assert kinds_of("Desc_LiquidOil_C") == ["node", "well"]

    assert maplink.layers_for(["Desc_Coal_C"], ["node"]) == [
        "coalImpure",
        "coalNormal",
        "coalPure",
    ]
    assert all("Well" in t for t in maplink.layers_for(["Desc_NitrogenGas_C"], ["well"]))


# ---------------------------------------------------- the place `at` takes


def _fixed_state(monkeypatch, st):
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    monkeypatch.setattr(stools, "_state", lambda save=None, world=None, as_of=None: st)


def test_at_takes_the_shared_place_vocabulary(monkeypatch, state, game):
    """`show_on_map` used to spell its place `target=` and resolve a set of its own.
    It now takes what every other place-taking tool takes, under the same name."""
    from satisfactory_mcp import server as srv

    _fixed_state(monkeypatch, state)
    for place in ("0,0", "me", "slab:0", state.conduit_runs[0].ident):
        out = srv.show_on_map(place)
        assert out.startswith("# ") and "local map: " in out, place


def test_a_node_place_lights_up_that_nodes_own_resource(monkeypatch, state, game):
    """The overlay is read off the node rather than guessed, which is the one thing
    `at` needs beyond the point the resolver hands back."""
    from satisfactory_mcp import server as srv
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    _fixed_state(monkeypatch, state)
    node = next(n for n in nodes_mod.load_nodes().nodes if n["resource"] == "Desc_Coal_C")
    out = srv.show_on_map("node:" + node["instance"].rsplit(".", 1)[-1])
    assert "Coal" in out
    assert "# layers: " in out and "coal" in out.split("# layers: ")[1]


def test_a_resource_is_show_on_maps_own_kind_and_says_so(monkeypatch, state, game):
    """A resource name centres on the centroid of EVERY node of it, which is a viewport
    and not a place -- so it is prefixed like every other kind and lives only here."""
    from satisfactory_mcp import server as srv

    _fixed_state(monkeypatch, state)
    out = srv.show_on_map("resource:Crude Oil")
    assert "node(s)" in out and "centroid of every node" in out
    # The bare name is gone rather than kept as a second spelling: unprefixed, it is a
    # factory label like anywhere else, and there is no factory by that name.
    refused = srv.show_on_map("Crude Oil")
    assert refused.startswith("! ") and "does not name a place" in refused


def test_show_ref_names_a_node_by_its_leaf():
    assert maplink.show_ref(node="Persistent_Level:PersistentLevel.BP_ResourceNode1") == (
        "node:BP_ResourceNode1"
    )
    assert maplink.show_ref(node="x", run="chain:1") == "node:x"
