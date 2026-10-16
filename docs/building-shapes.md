# Building shapes

The web map draws machines, extractors, generators, storage boxes, fluid buffers, belt
splitters/mergers, pipe fittings (junctions, pumps, valves), hypertube entrances and the one-off
facilities (the HUB, the AWESOME Sink, the MAM and the like) as an approximation of their real
top-down shape: the footprint, up to four lighter height bands inside it, and a notch at each
belt and pipe port.
This page says where those outlines and ports come from and how far to trust them.

## Where they come from

`tools/gen_building_shapes.py` reads the installed game (the `gen` extra, because the
container needs `ooz`) and writes `data/building_shapes.json`. It is a committed table like the
node tables: derived measurements (a few dozen vertices per class and the connector positions),
not meshes, and the server needs no game assets to serve it. It is written one line per class
(70 KB for 62 classes), so a regeneration diffs by class rather than by number. The `_meta` block carries the
build it was cut from, and `provenance.PINNED_ARTIFACTS` lists it, so a game update reports it
as stale alongside the other tables. Rerun the tool after an update:

    uv run --extra gen python tools/gen_building_shapes.py

The classes are every `Build_*` class under `FactoryGame/Buildable/` that the save projection
files as a machine, extractor, generator, attachment or pipe fitting (`registers.*_HINTS`) or
lists as storage, a fluid buffer, a facility or a hypertube entrance. A class is keyed by the name its package
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

Step 4 alone draws a staircase's slant: a wall two degrees off, or a grille a cell proud of
it, comes out as a tilted, jittery edge on what is a straight-walled machine. So each ring is
then regularised (below), footprint and bands alike.

### Regular outlines

`tools/building_shapes/regular.py` offers each traced ring in up to two regular forms and keeps
one only while it still covers the blob's raster about as well as the plain simplification:
its IoU with the raster may be at most 0.03 lower (`max_loss`). Below that the plain ring is
kept, which is how irregular parts (the HUB's legs, the Space Elevator's arms, the Alien Power
Augmenter's star) keep their own shape. Over the 62 classes that kept 193 rings
straight, 6 round and 16 plain, for a mean IoU loss of 0.005 (worst 0.030; 58 rings cover the
raster better than before).

#### Straight walls

`straighten.py` works in the building's own frame, where the walls run along its axes:

1. The cell-edge trace is simplified at one cell (Douglas-Peucker), from its sharpest corner.
2. An edge within 12 degrees of 0, 90, 180 or 270 degrees snaps to it, and one within 12
   degrees of a diagonal to that 45-degree heading. Consecutive edges with one heading are one
   wall, placed at the staircase's own mean offset, each step weighted by its run along the
   wall (risers count for nothing), so the wall sits where the cells say rather than at a DP
   vertex.
3. Two parallel walls at most 1.5 cells apart, joined by a step at most 3 cells long, become
   one wall (a jog from a bolt or a grille).
4. A slanted edge at most 6 cells long between two walls at right angles is cut, squaring the
   corner: these are mostly the closing's bevels.
5. A band's wall within 1.5 cells of a parallel wall of the level below is put on it, so flush
   walls are drawn as one line.
6. Walls meet at their intersection; walls that would meet more than 8 cells from the traced
   corner, or run nearly parallel, are joined by a short step instead.

The snapping angle is then widened to 20 and 30 degrees (`snap_levels`), and the widest that
still passes the IoU check wins: a Manufacturer's band traced from a sloped roof has 3 m
edges 20 degrees off, which only the wider snap squares, while a real 30-degree slant costs
too much area to snap and stays. Where the vertex budget is exceeded, the Douglas-Peucker
tolerance and the arc step grow by a quarter until it fits.

#### Round parts

A cell staircase strays from the curve it follows by half a cell at its corners, so circles
are fitted through the midpoints of its steps, resampled every half cell (`rounds.py`).
Measured on traced discs of 5 to 50 cells' radius, that leaves 0.14 to 0.18 cells (rms)
between trace and circle, where a wall, a bevel and a wall turning together stray 0.3 cells
or more.

- A ring whose whole trace sits within 2% of the radius (and never less than 0.3 cells) of
  one circle is drawn as a regular 24-gon of the circle's area, flat sides facing the axes,
  when that covers the raster no worse than the straight form does. Its centre is put on a
  mirror axis (below) within a cell of it. Tanks, the Blender's vessel and the coal
  generator's chimney come out this way.
- Within a ring, a run of at least three simplified edges whose trace sits within 0.25 cells
  (rms) of a circle of at least 8 cells' radius, bulging out by at least 60 degrees, is drawn
  as that circle's arc, a vertex every 15 degrees; back-to-back arcs on nearly one circle are
  joined. The Refinery's tank, the Foundry's furnace ends and the Smelter's nose are such
  arcs. Arcs that bite in are left to the walls: on these traces they are the closing's
  rounded inside corners, and drawn as arcs they looked like bites taken out of the building.

#### Mirror symmetry

Most machines are symmetric about a line along one of their own axes, not necessarily through
the actor origin. `mirror.py` searches, on the footprint's raster, the line along each axis
(on a half-cell lattice, within 2 m of the raster's middle) whose mirror image matches the
raster best; it counts where that match is an IoU of at least 0.93. Each level (the footprint,
then each band) whose own raster also matches about that line at 0.93 is made exactly
symmetric: a ring across the line keeps whichever half covers the raster better plus its
mirror image, a ring on one side takes its partner's mirror image. A Constructor's housing,
which is not symmetric (a ladder on one side), scores 0.78 and keeps its own shape.

#### Nesting and ports

A band is traced only where it lies inside the level below (its raster is cut to the cells
the lower level's rings cover), a regular form may cover at most 16 cells or 1% of its own
area more outside that level than the plain ring did, and a vertex that still ends up outside
is moved onto the nearest edge of the level below. A port within 1 m of the plain footprint
may end at most 30 cm further from the regular one, so a notch a pipe plugs into stays (the
Fuel Generator's fuel inlet).

### Height bands

An outline alone says how much ground a building takes, not what it looks like: a Refinery and
a Coal Generator are both long rectangles. So the generator keeps each triangle's height, takes
the highest point over every 10 cm cell, and cuts up to four bands out of that top-down height
map (`tools/building_shapes/bands.py`), each traced like the footprint with its own vertex
budget (32, 28, 24, 20, lowest first).

Fixed heights do not work across classes: a 2 m and 55%-of-height pair of cuts gave a
Constructor and a Refinery the same two bands, and the Refinery, whose body above 2 m is nearly
its whole footprint, ended up with one. The cuts are therefore the class's own natural breaks:

1. The height map is opened over 30 cm (railings, thin pipes and cables over a walkway are not
   mass) and closed over 40 cm, as grey-scale morphology, so each level set of the result is
   exactly the band that would be drawn at that height; the cuts are chosen on what is drawn,
   not on the mesh's speckle.
2. The cells' heights, capped at their 99th percentile so one antenna does not stretch the
   scale, are binned at 25 cm (coarser past 160 bins) into an area-per-height histogram.
3. Fisher-Jenks breaks split the histogram into the ground and up to four bands, each group as
   tight around its own mean height as possible: the plateaus a building is made of (a base
   plate, a housing, a roof deck, a tower) come out as groups, wherever they sit.
4. A split only counts if it shows. Every band but the top one must leave at least 6% of its
   area (and 2 m²) visible under the next band, in strips at least 60 cm wide, which a thin
   ledge round a block is not; consecutive floors must be at least 1 m apart; no band starts
   under 2 m (walkways and base plates are the footprint's); the top band covers 2 m². The
   search takes the most bands that can meet this, so a box gets none and a stepped tower
   gets four.

After tracing, a band covering more than 90% of the band kept below it is still dropped, and a
band keeps at most its six largest parts. `tier` is the break's rank (1 the lowest); a dropped
band leaves a gap rather than renumbering the ones above it, so a higher tier always reads
lighter.

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

Height bands are drawn over the footprint as a white veil with a thin dark edge, in even steps:
tier n lightens the fill by n x 0.15 in all (0.15, 0.30, 0.45, 0.60), at 40% of that on an idle
building whose fill is faded. Bands nest and their veils stack, so each band's own opacity is
what takes the band under it to its tier's lightness (`bandVeil`); a tier dropped in between
does not leave the one above it darker. The veil keeps the layer's hue and lightens it, so
lower reads darker and higher lighter, and the status colours (blocked yellow, stopped red)
stay on the outline alone. Bands are hidden below 1.5 px per metre and take no clicks; a click
on one lands on the building.

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

Hypertubes are a route layer of their own, `hypertubes`, drawn from their splines the way pipes
are (`/api/hypertubes`). Their entrances are outlined in that layer from this table. The
entrance's components trace to a small box behind the actor origin (about 1.6 x 2.4 m), so the
outline marks where the tube starts; whether the rest of the entrance is a mesh this reader
misses has not been checked.

Placed classes in the reference world that are still not drawn, on purpose: belt and pipe
supports (conveyor poles, ceiling and wall mounts, stackable poles, pipeline supports and their
wall variants), pipe flow indicators, foundation passthroughs, hypertube supports, lights, ladders, the
toilet, the doors, gates and conveyor walls placed as actors, and the HUB's own terminal and
work bench, which the HUB's outline already covers. Power poles and towers are the power
layer's.

## Limits

- One outline per class: a Mk2 storage container's two stacked inputs share a spot from above.
- The Fracking Smasher and Particle Accelerator outlines follow the meshes, which spill past
  their clearance boxes; the clearance box is still what the planner sizes with.
- No building icons are drawn at high zoom; the icon set is item art in `data/local/`, and
  buildings have no icon of their own there.
