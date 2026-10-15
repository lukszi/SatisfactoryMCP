# Building shapes

The web map draws machines, extractors, generators, storage boxes, fluid buffers and belt
splitters/mergers as an approximation of their real top-down shape, with a notch at each belt
and pipe port. This page says where those outlines and ports come from and how far to trust
them.

## Where they come from

`tools/gen_building_shapes.py` reads the installed game (the `gen` extra, because the
container needs `ooz`) and writes `data/building_shapes.json`. It is a committed table like the
node tables: derived measurements (a few dozen vertices per class and the connector positions),
not meshes, and the server needs no game assets to serve it. The `_meta` block carries the
build it was cut from, and `provenance.PINNED_ARTIFACTS` lists it, so a game update reports it
as stale alongside the other tables. Rerun the tool after an update:

    uv run --extra gen python tools/gen_building_shapes.py

The classes are every `Build_*` class under `FactoryGame/Buildable/` that the save projection
files as a machine, extractor, generator or attachment (`registers.*_HINTS`) or lists as storage
or a fluid buffer. A run takes a few seconds.

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
they would be specks. They carry their building's floor mark without a height, so the floor
filter keeps or drops them with the building and never ghosts them.

## Limits

- One outline per class: a Mk2 storage container's two stacked inputs share a spot from above.
- The Fracking Smasher and Particle Accelerator outlines follow the meshes, which spill past
  their clearance boxes; the clearance box is still what the planner sizes with.
- No building icons are drawn at high zoom; the icon set is item art in `data/local/`, and
  buildings have no icon of their own there.
