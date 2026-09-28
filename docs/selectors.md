# Selectors: one grammar for saying which

This surface asks the caller to point at things three times — **which resource nodes** may
feed a plan, **which machines** make up a factory, and **which place** a question is about.
Each has its own vocabulary, because the three name different kinds of thing. What they
must not have is three ways to spell the *same* thing, and this page is where that is
settled: the shared rules first, then each vocabulary in full, then the differences that
remain and are deliberate.

Implemented in `domain/spatial/select.py` (nodes), `domain/factories/select.py` (machines)
and `domain/spatial/origin.py` (places). Both selector modules are handed the world state
and call `origin.py` for the one construct all three share -- a circle around a place --
so the place table below is the whole of "where" for the entire surface.

## The rules every selector obeys

1. **Coordinates are metres.** The save is in centimetres; nothing on this surface is.
   A coordinate is always the pair `x,y`, in that order, and either may be negative or
   fractional.
2. **A comma separates coordinates, or alternatives. It is never a distance.**
3. **A radius follows `@`**: `<place>@<radius_m>`. It is required — there is no default
   radius, because a selector missing its radius is a truncated selector, not a small one.
4. **`:` separates a selector's kind from its value**, and the kind is case-insensitive.
5. **The player is `me`.** `player` and `here` mean the same thing wherever `me` is taken.
6. **An unresolvable selector is an error that names what would have worked.** Nothing on
   this surface widens its scope to compensate for a term it could not read.

`near:0,-2000,900` — a radius as a third comma value — was a second spelling of rule 3 and
is **retired**. It does not parse, on either side; the error prints the rewrite, because
the caller who meets it is usually a stored plan or a copied example:

```
near:'0,-2000,900' has no radius. Write near:0,-2000@900 instead. A circle is
near:<place>@<radius_m> -- e.g. near:-1069,-1273@200, near:me@500,
near:steel factory@150. The radius follows '@', never a comma
```

## Places — `at=`, `near=`, `site_at=`

A **place** is a single point, and there is exactly one vocabulary for one. Resolved by
`resolve_origin`, and taken by every tool that asks where: `describe_location(at=)`,
`show_on_map(at=)`, `site_plan(at=)`, `plan_factory(site_at=)`, `search_conduits(near=,
to=)`, `search_resource_nodes(near=)`, `storage(near=)` and `collected_from_world(near=)`
-- **and by the `near:` term of both selector languages below**, which is what makes the
place table the only table a reader needs for "where".

| place | resolves to | needs a save |
|---|---|---|
| `x,y` | that coordinate, in metres | no |
| `node:<instance>` | one resource node, by the id `search_resource_nodes` prints | no |
| `me` | the player pawn's position in the save | yes |
| `<factory name>` | the centroid of a named factory's machines | yes |
| `slab:<n>` | a foundation platform's tile mean, by the index `factory_map show=slabs` prints | yes |
| `chain:<n>` / `pipe:<n>` | the midpoint of a conduit run, by the ident `search_conduits` prints | yes |
| `plan:<name>` | a stored plan's recorded site origin (see `site_plan`) | yes |
| `pin:<n>` | a located pin: point, node, field centroid, machine, factory centroid, sited plan | yes |

The two map facts resolve with no save at all; the rest name what they are missing rather
than falling back. The point a place resolved to is echoed back with the name that
produced it, because `at=` can land somewhere the caller never typed.

`show_on_map(at=)` takes one kind **more**: `resource:Crude Oil` centres on the centroid
of every node of that resource and switches its overlays on. That is a viewport rather
than a place -- it names a *set*, and the centroid of a scattered set can be open water,
which the answer says -- so it is not in `resolve_origin` and no other tool accepts it.
Spelled with the `:` of rule 4 like everything else; the bare resource name it replaces
is retired, and unprefixed text is a factory label here as it is everywhere.

The local link `show_on_map` writes carries a `show=` key the page opens once it has
loaded, and it names the place in the same spelling as the table above: `show=node:<leaf>`
for a node, `show=chain:<n>` / `show=pipe:<n>` for a run, `show=label:<name>` for a named
factory. A node or a run is flown to, ringed in the finder pane and selected; a label
selects the factory. Other places carry no `show=`, only the centre. `maplink.show_ref`
spells all four.

## Which nodes — `sources=`

A **source spec** is a list of selectors, and it is what every `sources=` parameter takes:
`plan_factory`, `plan_layout`, `diff_vs_save`, `commission_plan`, `explain_byproducts`,
`rank_unlocks`, `advise_hard_drive_pick`, `search_resource_nodes` and `rank_build_sites`.

| selector | meaning |
|---|---|
| `north`, `northeast`, … | compass hemisphere, or a 60° cone when an origin is supplied |
| `region:Northern Forest` | named region; a bare name also works |
| `grid:X3Y4` | one exact 1.024 km biome grid cell |
| `node:BP_ResourceNode30_103` | one specific node, repeatable |
| `pin:<n>` | a node pin's node, or every node of a field pin |
| `near:<place>@<radius_m>` | a circle around any place in the table above |
| `bbox:<x1>,<y1>,<x2>,<y2>` | a rectangle, corners in metres |
| `resource:Crude Oil` | filter: resource type |
| `purity:pure\|normal\|impure\|all` | filter: purity |
| `kind:node\|well_sat\|geyser\|all` | filter: node kind |
| `all` | every node on the map |

The last three are also plain parameters on `search_resource_nodes` — `resource=`,
`purity=`, `kind=` — spelled exactly as the terms they stand in for, so either form reads
the same and neither is a second vocabulary.

**Locations union, filters intersect.** `["north", "resource:Crude Oil"]` is crude oil in
the northern half; `["region:Spire Coast", "near:120,-2020@1500"]` is the union of two
areas. Omitting locations entirely is a legitimate whole-map query; a location that fails
to resolve returns **nothing**, and says so.

## Which machines — `select=`, `factory=`

A **machine select** is a list of terms, taken by `select_machines(select=)`,
`name_factory(select=)` and `amend_factory(add=, drop=)`. The tools that address one
factory at a time -- `factory_query`, `factory_health`, `factory_floors`, `plan_layout`,
`diff_vs_save` -- take a single such term as `factory=`, so a label name and a selector
are interchangeable there.

| term | meaning |
|---|---|
| `product:Steel Pipe` | everything making it, anywhere |
| `recipe:Alternate: Solid Steel Ingot` | by recipe name or id, substring allowed |
| `building:Foundry` | by display name or class |
| `near:<place>@<radius_m>` | a circle around any place in the table above |
| `base:<n>` | a power island, largest first |
| `line:<n>` | a material component, largest first |
| `slab:<n>` | a foundation platform, by its own printed index |
| `proposal:<n>` | the nth cluster from `propose_factories` |
| `label:<name>` | what a named factory already covers |
| `machine:<instance>` | named instances, exactly as the tools print them |
| `pin:<n>` | a machine pin's machine, or what a factory pin's label covers |
| `all` | every machine |

**Terms are ANDed; commas inside one term are ORed; a leading `-` excludes.** So
`["product:Concrete", "near:-1059,-1257@150"]` is the concrete feed at one site, and
`["base:0", "-label:steel factory"]` is the base minus what is already named. Intersection
rather than union because carving is subtractive: the player starts from something too big.

Two modifiers, because a factory is delimited from either end: `split` keeps only the
largest spatial cluster, `expand` grows the result to whole material components.
Exclusions apply **after** expanding.

**`base:`, `line:`, `slab:` and `proposal:` are positions in size-ordered lists rebuilt
from the save on every call.** They shift when you build. Read an index and name what it
selected in the same breath; never store one. A label is durable because it holds machine
ids.

## Pins — `pin:<n>`

A pin is a numbered handle the page places on a plan, a process, a machine, a factory, a
field, a node or a point (docs/planner-p3_contract.md §4). `pin:<n>` is accepted wherever the
thing it pins could stand, and `domain/planning/pins.py` is the only code that parses it:

| grammar | resolver | kinds accepted | becomes |
|---|---|---|---|
| place (`at=`, `near=`, `to=`, `site_at=`, `near:pin:<n>@r`) | `resolve_origin` | point, node, field, machine, factory, sited plan | the pin's position |
| node sources | `select_nodes` | node, field | `node:<id>` per node |
| machine select, `factory=` | `select_machines` (also `-pin:<n>`) | machine, factory | `machine:<instance>` / `label:<name>` |
| `plan=` | `recall.plan_ref` | plan | the plan key |
| `required=`, `exclude_recipes=` | `pins.canonical` at the tool or route | process | the recipe class id |

Every expansion is echoed as `pin:3 = node:BP_ResourceNode30_103 (Iron Ore, pure)`. A pin
that cannot stand somewhere is refused in words: `pin:9 does not exist (pins run to pin:6)`,
`pin:3 was deleted`, `pin:4 is a process: it cannot stand for resource nodes`, `pin:2 is a
point: write near:pin:2@<radius_m>`, `pin:5 is gone: plan forgotten`, `pin:1 is a plan with no
site`. `pin:` inside `bbox:` or `grid:` is not a term.

**Stored plans never hold `pin:`.** `plan_factory` and the web writes (`create_plan`,
`push_args`, `push_ops`) rewrite `sources`, `required` and `banned` members to what the pin
stands for before the store, and `near:pin:3@200` becomes `near:<x>,<y>@200`. A plan therefore
reads the same after its pin is deleted.

## What the two selector languages still do not share

Named here so that the next reader knows it is a known state and not an oversight.

- **`near:` no longer differs.** Both selector modules hand the whole world state to
  `resolve_origin` and take the same eight places, so a `near:` term copied from one
  works in the other. Neither module resolves a place itself; a second resolver is what
  the divergence was made of.
- **Indices are machine-side only.** `base:`, `line:`, `slab:` and `proposal:` have no
  meaning over resource nodes, which are map facts rather than save facts. `slab:<n>` is
  the exception that proves it: as a *place* it works everywhere, and only as a
  standalone selector -- "the machines standing on that platform" -- is it machine-side.
- **`resource:` means two things, on purpose.** As a node *filter* it narrows a selection
  to one resource type; as `show_on_map(at=)`'s own place kind it centres on the centroid
  of every node of that resource. Same noun, one selecting and one pointing.
- **`kind` is this language's word, and now the surface's only one.** It meant five
  unrelated vocabularies as a tool parameter -- a recipe class, a building category, a
  container's medium, a conduit's medium and this. Four of the five were renamed after what
  they actually filter (`recipe_kind=`, `building_kind=`, `container_kind=`,
  `conduit_kind=`); `search_resource_nodes(kind=)` kept the word because it is shorthand for
  the `kind:` term above, exactly as its `resource=` and `purity=` are shorthand for
  `resource:` and `purity:`. Renaming the parameter alone would have given one tool two
  spellings of one filter, which is the defect and not the fix.
- **`all` in a filter means no filter.** `kind:all`, `purity:all` and `resource:all` narrow
  nothing, which is what `all` means on every tool that takes a kind. `all` on its own is
  still a location: every node on the map.
