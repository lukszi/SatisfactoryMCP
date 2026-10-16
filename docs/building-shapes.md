# Building shapes

The web map draws machines, extractors, generators, storage boxes, fluid buffers, belt
splitters/mergers, pipe fittings (junctions, pumps, valves) and the one-off facilities (the HUB,
the AWESOME Sink, the MAM and the like) as an approximation of their real top-down shape: the
footprint, up to two lighter height bands inside it, and a notch at each belt and pipe port.
This page says where those outlines and ports come from and how far to trust them.

## Where they come from

`tools/gen_building_shapes.py` reads the installed game (the `gen` extra, because the
container needs `ooz`) and writes `data/building_shapes.json`. It is a committed table like the
node tables: derived measurements (a few dozen vertices per class and the connector positions),
not meshes, and the server needs no game assets to serve it. It is written one line per class
(56 KB for 61 classes), so a regeneration diffs by class rather than by number. The `_meta` block carries the
build it was cut from, and `provenance.PINNED_ARTIFACTS` lists it, so a game update reports it
as stale alongside the other tables. Rerun the tool after an update:

    uv run --extra gen python tools/gen_building_shapes.py

The classes are every `Build_*` class under `FactoryGame/Buildable/` that the save projection
files as a machine, extractor, generator, attachment or pipe fitting (`registers.*_HINTS`) or
lists as storage, a fluid buffer or a facility. A class is keyed by the name its package
generates, which is not always the package's own spelling: the Mk.2 pump's package is
`Build_PipelinePumpMK2`, its class `Build_PipelinePumpMk2_C`. A run takes about 20 seconds.

### Components

A blueprint class's components come from its construction-script tree
(`core/gameassets/packages/blueprint.py`):

- each `SCS_Node` names a component template and its child nodes; a template's
  `RelativeLocation`/`RelativeRotation`/`RelativeScale3D` is composed down the tree, starting
  from identity for a node attached to the native root;
- a parent blueprint class (the export's `SuperIndex`) contributes its tree first, and an
  `InheritableComponentHandler` template overrides an inherited component by name;
- meshes placed as lightweight instances (splitters and mergers, among others) are in the
  `AbstractInstanceDataObject` the class default points at, one `FInstanceData` per mesh with
  its own `RelativeTransform` quaternion.

Every component with a `StaticMesh` property counts: the coloured mesh proxy, the
vertex-animated parts and any plain static mesh. Skeletal meshes are not read; where a class has
one (the miners' drill), its static body is enough for an outline.

### Outline

LOD 0 of each mesh (`core/gameassets/staticmesh.py`) is placed in the actor frame and flattened
onto the ground plane. `tools/building_shapes/silhouette.py` then

1. samples every triangle on a lattice no coarser than a 10 cm cell, so a wall seen edge-on
   still marks its line;
2. closes the grid over 40 cm, which joins parts that touch only visually, and fills holes;
3. keeps each blob at least 2% of the largest one's area, so a detached chimney survives and a
   stray bolt does not;
4. traces each blob's outer edge along cell edges and simplifies it (Douglas-Peucker, starting
   at one cell and widening) to at most 48 vertices.

Several blobs are sent as several rings and drawn as a multipolygon, never as a ring with
holes. The closing bevels inside corners by about its radius.

### Height bands

An outline alone says how much ground a building takes, not what it looks like: a Refinery and
a Coal Generator are both long rectangles. So the generator keeps each triangle's height, takes
the highest point over every 10 cm cell, and cuts up to two bands out of that top-down height
map, each traced like the footprint with its own vertex budget:

| tier | parts above | budget | reads as |
| --- | --- | --- | --- |
| footprint | anything | 48 | the base plate, walkways, everything that stands on the floor |
| 1 | 2 m | 32 | the body: the mass a player walks around |
| 2 | 55% of the class's height, at least 4 m | 20 | chimneys, towers, tanks, hoppers |

The class's height is the 99th percentile of its cell tops, so one antenna does not set it.
Before closing, a band is opened over 30 cm: railings, thin pipes and cables over a walkway are
not mass and would otherwise join the body to the edge of the footprint. A band with less than
0.5 m² left, or covering more than 90% of the band kept below it, adds nothing and is left out:
a storage container is a box, so it has no body band, and a Refinery's body above 2 m is
nearly all of its footprint, so it gets its tall band only.

Alternatives weighed: crease lines from the mesh (sharp edges seen from above) are dense and
noisy on these meshes, where every bolt and grille is modelled; a small baked raster per class
(32 x 32, 4-bit) would be about as large as the bands but needs a rotated image layer the canvas
renderer does not have. Bands reuse the outline's pipeline and drawing.

Seven building meshes (Assembler, Manufacturer, Refinery, Blender, Converter, Quantum Encoder,
Particle Accelerator) carry more than 64 sockets, past the socket count `staticmesh` derives the
LOD offset from; the tool retries those at `38 + 4 * sockets`, the same rule without the cap.

### Ports

Ports are the connection components: `FGFactoryConnectionComponent` for belts,
`FGPipeConnectionComponent`/`FGPipeConnectionFactory` for pipes. A component's +X axis is its
facing. Flow is the component's own property, defaulted the way the game defaults it:

| Kind | Property | Value | `flow` |
| --- | --- | --- | --- |
| belt | `mDirection` | absent (`FCD_INPUT`) | `in` |
| belt | `mDirection` | `FCD_OUTPUT` | `out` |
| belt | `mDirection` | `FCD_ANY` | `any` |
| belt | `mDirection` | `FCD_SNAP_ONLY` | not a port |
| pipe | `mPipeConnectionType` | `PCT_CONSUMER` | `in` |
| pipe | `mPipeConnectionType` | `PCT_PRODUCER` | `out` |
| pipe | `mPipeConnectionType` | absent (`PCT_ANY`) | `any` |

## Frame

Everything is metres in the actor's own frame: +X is the building's forward, +Y its right, the
save's axes when its yaw is 0. The map places a point `(dx, dy)` at
`(x + dx·cos(yaw) − dy·sin(yaw), y + dx·sin(yaw) + dy·cos(yaw))`, the rule `footprintCorners`
already used.

That frame was checked against a real save rather than assumed: every port of every placed
machine, extractor, generator, attachment and container was put in the world this way and
matched to the nearest belt or pipe end. Of 4,819 belt ports, 76% sit within 30 cm of a belt end
(median 0.4 cm); of 238 pipe ports, 79% (median 0.4 cm). The remainder are ports with nothing
connected. Mirroring Y moves the pipe median to 4.3 m, so the handedness is not ambiguous.

## Fallback

A class the table does not cover is drawn from its clearance boxes (`mClearanceData`), each box
on its own at its own offset (`footprint.clearance_rings`), with whatever ports the table has
for it. `Footprint` also carries the union box's offset from the actor origin now
(`offset_x_m`, `offset_y_m`), which earlier versions dropped. A class with neither still draws
the measured `w_m` x `l_m` rectangle, or the page's fixed square without one.

## On the wire and on the page

`/api/machines`, `/api/storage` and `/api/belts` each carry a `shapes` object keyed by class for
the classes in that response, so an outline crosses the wire once per class rather than once
per actor ([web-wire.md](web-wire.md), "Placements"). `source` says `mesh` or `clearance`.

`map/drawn/outlines.ts` draws the rings rotated by yaw, with the same styling and click
behaviour the rectangles had. Notches are small triangles (pointing out for an output, in for
an input) or diamonds (either way), in the belt or pipe tone, hidden below 3 px per metre where
they would be specks.

Height bands are drawn over the footprint as a white veil with a thin dark edge: 0.22 for the
body, 0.42 for the tall parts, at 40% of that on an idle building whose fill is faded. The veil
keeps the layer's hue and lightens it, so lower reads darker and higher lighter, and the status
colours (blocked yellow, stopped red) stay on the outline alone. Bands are hidden below 1.5 px
per metre and take no clicks; a click on one lands on the building.

Notches and bands carry their building's floor mark without a height, so the floor filter keeps
or drops them with the building and never ghosts them.

### Buildings under an upper deck

Each layer is drawn lowest first, but a top-down map still paints a building on an upper deck
over the one beneath it, and the layers stack in a fixed order (generators over machines). On
the reference world seven Packagers stand under Fuel Generators 32 m above them, every one
fully covered, so they looked missing. Every building, facility and container therefore also
gets an x-ray line: a dashed copy of its outline, in its outline colour (so in its status
colour), in its own pane above the routes and buildings. Over its own building it falls on its
own outline and adds nothing; where something higher covers it, it is the line left showing.
Floor mode is still the way to look at one deck alone.

### Fittings and facilities

Pipe junctions, pumps and valves ride with `/api/pipes` and are drawn in the pipes layer, as
the splitters are in the belts layer. The one-off facilities ride with `/api/machines` and get
their own layer, `facilities`, in teal. Neither is listed by `/api/floors`, so the floor filter
places them by where they stand, as it places storage.

Placed classes in the reference world that are still not drawn, on purpose: belt and pipe
supports (conveyor poles, ceiling and wall mounts, stackable poles, pipeline supports and their
wall variants), pipe flow indicators, foundation passthroughs, hypertubes (a spline like a pipe,
with supports and entrances; a route layer of its own, not an outline), lights, ladders, the
toilet, the doors, gates and conveyor walls placed as actors, and the HUB's own terminal and
work bench, which the HUB's outline already covers. Power poles and towers are the power
layer's.

## Limits

- One outline per class: a Mk2 storage container's two stacked inputs share a spot from above.
- The Fracking Smasher and Particle Accelerator outlines follow the meshes, which spill past
  their clearance boxes; the clearance box is still what the planner sizes with.
- No building icons are drawn at high zoom; the icon set is item art in `data/local/`, and
  buildings have no icon of their own there.
