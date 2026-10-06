/* Both networks: the belts and the pipes, drawn as the routes they actually take.
 *
 * One module for two layers because they share a grammar -- one colour family each, tier as a
 * value step inside it, width physical and floored at a hairline -- and because three passes
 * run over them BY NAME: the pixel restyle on zoom, the sink that keeps a run from stealing a
 * machine's click, and the width table that is the only thing telling them apart.
 *
 * The power wires are drawn by power-wires.ts and go through those same three passes: `power` is in
 * ROUTE_LAYERS and ROUTE_WIDTH_M at the bottom of this file. The import goes one way --
 * power-wires.ts reads `sinkRoutes` from here and nothing here reads power-wires.ts. */

import { code, popup } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, layer } from "../layers";
import { footprintCorners, map, pixelsPerMetre } from "../map";
import { raiseNodeDots } from "./markers";
import { declareColours } from "../palette";
import { tone } from "../map-tone";
import { registerFetch } from "../../app/registry";
import { state } from "../../app/state";

import type { Row } from "../../kit/dom";

import type {
  BeltRow,
  BeltsResponse,
  PipeFlowBasis,
  PipeRow,
  PipesResponse,
} from "../../api/shapes";
/* The four the SERVER does not name: the page's own words for the tuples in the payloads
 * above, plus `PointM`, which is the projected pair nothing on the wire carries. */
import type { Point3M, PointM, RouteCurveM, SpanCurveM } from "../geometry";

/* The row that names the thing the reader just clicked, for the three route popups whose
 * subject is a resolved class.
 *
 * Both `name` and `cls` are nullable, so a plain `name || cls` evaluates to null on a torn row,
 * popup() drops a null row, and the card loses its TITLE while keeping every coordinate under
 * it. The row is therefore always emitted, and the last branch is a statement rather than an
 * invented name -- which would be indistinguishable from a resolved one. */
function titleRow(key: string, name: string | null, cls: string | null): Row {
  return [key, name || cls || "class not recorded in this projection"];
}

/* The curve, drawn: how a spline becomes a polyline, and how many pieces that is worth.
 *
 * A belt or pipe spline stores a tangent either side of every control point, so a run the
 * player laid as an arc is an arc -- a chord through the corners is out by up to 16.4 m on a
 * single belt piece.
 *
 * THE SUBDIVISION IS ZOOM-DEPENDENT, which is why it is affordable. Tessellating to a fixed
 * quality would put the most points on the canvas at the whole-world view, exactly where all
 * 3,085 belt pieces are on screen at once; asking "how far is this curve from its chord IN
 * PIXELS, right now" instead adds no points at all below zoom -1, and grows the whole network
 * from 6,691 line points to 8,712 at maxZoom.
 *
 * A SPAN WITH NO CURVE IS NEVER TOUCHED: the server sends null for a straight span and for a
 * route with no bend anywhere in it, so a straight run is the same two points at every zoom.
 */

/* Half a pixel: below this a bend and the line through it land on the same pixels. Not a
 * quarter -- the canvas is not drawing sub-pixel geometry to that accuracy anyway, and the
 * error halves the step count. */
var CURVE_TOLERANCE_PX = 0.5;

/* A ceiling, so that the bound on the work does not come from the data. Generous rather than
 * tight: cutting it from 16 to 8 moves the whole world's line-point count by 58 out of 8,712,
 * because span flatness has a median of 3.9 cm and only 5% exceed 69 cm. */
var CURVE_MAX_STEPS = 8;

/* How far one span's curve can leave the straight line between its ends, in metres.
 *
 * The classic cubic flatness bound, via the Bezier form: a Hermite span's inner control points
 * are `p0 + leave/3` and `p1 - arrive/3`, and the curve stays within three quarters of the
 * further one's distance from the chord. An upper bound, so it can only over-subdivide.
 *
 * Measured IN THE PLAN, x and y only, because that is what this map draws -- including z would
 * demand eight subdivisions of a conveyor lift, which occupies one pixel. */
function spanFlatnessM(p0: Point3M, p1: Point3M, span: SpanCurveM): number {
  var ax = p0[0];
  var ay = p0[1];
  var vx = p1[0] - ax;
  var vy = p1[1] - ay;
  var chord = Math.sqrt(vx * vx + vy * vy);
  var b1x = ax + span[0][0] / 3;
  var b1y = ay + span[0][1] / 3;
  var b2x = p1[0] - span[1][0] / 3;
  var b2y = p1[1] - span[1][1] / 3;
  if (!(chord > 0)) {
    // Coincident ends -- the joint where a lift meets its belt. There is no chord to measure
    // against, so the control points' own offset is the whole of the departure.
    var d1 = Math.hypot(b1x - ax, b1y - ay);
    var d2 = Math.hypot(b2x - ax, b2y - ay);
    return 0.75 * Math.max(d1, d2);
  }
  var off1 = Math.abs((b1x - ax) * vy - (b1y - ay) * vx) / chord;
  var off2 = Math.abs((b2x - ax) * vy - (b2y - ay) * vx) / chord;
  return 0.75 * Math.max(off1, off2);
}

/* How many straight pieces one span is worth at this scale. 1 means "draw the chord".
 *
 * A cubic subdivided into n uniform pieces has an error of about `flatness / n^2`, so the n
 * that puts that under the tolerance is the square root of the ratio -- which is why a curve
 * ten times bigger costs three times the points and not ten. */
function spanSteps(flat_m: number, ppm: number): number {
  var px = flat_m * ppm;
  if (!(px > CURVE_TOLERANCE_PX)) return 1;
  return Math.min(CURVE_MAX_STEPS, Math.ceil(Math.sqrt(px / CURVE_TOLERANCE_PX)));
}

/* One point along a Hermite span, in game metres. The tangents arrive in the same space and
 * units as the points -- which is what `/api/belts` promises about `curve_m` -- so this is the
 * plain basis, and the y-flip below can be applied to the RESULT rather than to the inputs. */
function hermite(p0: Point3M, p1: Point3M, span: SpanCurveM, t: number): PointM {
  var t2 = t * t;
  var t3 = t2 * t;
  var h00 = 2 * t3 - 3 * t2 + 1;
  var h10 = t3 - 2 * t2 + t;
  var h01 = -2 * t3 + 3 * t2;
  var h11 = t3 - t2;
  return [
    h00 * p0[0] + h10 * span[0][0] + h01 * p1[0] + h11 * span[1][0],
    h00 * p0[1] + h10 * span[0][1] + h01 * p1[1] + h11 * span[1][1],
  ];
}

/* A route as the latlngs Leaflet draws, tessellated for the scale given, plus the step counts.
 * The `[-y, x]` flip is applied to the tessellated result rather than to the spline, so the
 * curve is computed in game metres and flipped once. */
function routeLatLngs(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number
): { latlngs: L.LatLngTuple[]; steps: number[] } {
  var latlngs: L.LatLngTuple[] = [[-points_m[0]![1], points_m[0]![0]]];
  var steps: number[] = [];
  for (var i = 0; i < points_m.length - 1; i++) {
    var a = points_m[i]!;
    var b = points_m[i + 1]!;
    var span = curve_m ? curve_m[i] : null;
    var n = span ? spanSteps(spanFlatnessM(a, b, span), ppm) : 1;
    steps.push(n);
    for (var k = 1; k < n; k++) {
      var q = hermite(a, b, span!, k / n);
      latlngs.push([-q[1], q[0]]);
    }
    latlngs.push([-b[1], b[0]]);
  }
  return { latlngs: latlngs, steps: steps };
}

/* One route as a drawn polyline, carrying the spline it was tessellated from. Re-tessellating
 * at a new zoom needs the SOURCE, and the drawn latlngs are not it -- they are already an
 * approximation, and subdividing them again would converge on that approximation rather than
 * on the curve. A route with no curve still gets a `_route`, at no cost: `steps` comes back all
 * ones and the zoom pass never touches it again. */
function routePolyline(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number,
  options: L.PolylineOptions
): L.Polyline {
  var shape = routeLatLngs(points_m, curve_m, ppm);
  var piece = L.polyline(shape.latlngs, options);
  piece._route = { points_m: points_m, curve_m: curve_m, steps: shape.steps };
  return piece;
}

/* Redraw one route for a new scale, and say whether it actually moved. Guarded on the step
 * counts rather than on the zoom, because most pieces do not change at most zoom steps: a run
 * with no bend never changes at all, and a gentle one holds the same subdivision across several
 * steps. At world zoom the guard skips every one of the 3,588 routes. */
function retessellate(piece: L.Polyline, ppm: number): boolean {
  var route = piece._route;
  if (!route || !route.curve_m) return false;
  var shape = routeLatLngs(route.points_m, route.curve_m, ppm);
  var same = shape.steps.length === route.steps.length;
  for (var i = 0; same && i < shape.steps.length; i++) same = shape.steps[i] === route.steps[i];
  if (same) return false;
  route.steps = shape.steps;
  piece.setLatLngs(shape.latlngs);
  return true;
}

/* The conveyor network, drawn as the routes it actually takes.
 *
 * ONE colour family for the whole network, and tier as a VALUE step inside it. Tier cannot be a
 * WIDTH step because width is physical here and every belt is the same two metres across, Mk1
 * to Mk5 -- drawing a Mk5 wider would contradict the same map's machines, drawn at their
 * measured footprint.
 *
 * A LIFT gets a ring instead of a line, because a line is not available: a lift is exactly
 * vertical (measured server-side -- all 302 on the reference world have zero horizontal
 * extent), so its top-down polyline is one point and draws nothing at all. A class the docs
 * dump has no entry for gets a `lift` of NULL rather than false, and earns the same ring on
 * different evidence -- "this route covers no ground" -- which the popup says out loud,
 * because a reader cannot tell a measured ring from an inferred one by looking.
 *
 * A SPLITTER or MERGER gets a square, in this layer and in no other: it is in no other payload,
 * and without it a belt-only view has a four-metre hole at every one of a world's 848
 * junctions.
 */
/* Mid steel, and mid because this is the one layer with no ground of its own: it has to read
 * over the dark concrete it mostly runs on AND over pale sand where it crosses country. A
 * near-white line does the first and vanishes into the second. */
/* The three tier tones are one step of value either side of the middle one, which stays the
 * network's colour and the swatch in the layer control. The step is dE 15.6 between slowest and
 * fastest and it is the page's house step -- the pipes' 15.1 below, the storage pair's 16.7 and
 * the poles' 16.0 all match it. Every one of those is a ramp INSIDE one module, which is why
 * palette.ts compares across modules and never within one.
 *
 * The belt steel is the page's oldest network colour and was never measured against anything;
 * everything else moves around it. Its nearest cross-owner neighbour is the limestone dot, at
 * dE 19.3 from this middle tone and 13.2 from the fast one -- that last pair is a filled disc
 * against a stroked line and is DISCHARGED in palette.ts at its measured distance. */
var BELTS = declareColours("routes", {
  belts: "#93a5b4",
  "belt slow": "#7f8f9d",
  "belt fast": "#a7b9c7",
  // The hole the ring is drawn around, and near-black because a hole can always get darker:
  // dE 21.7 from Abyss Cliffs, 34.8 from the concrete, nothing else within 24.
  "lift fill": "#0e1116",
});
var BELT_COLOUR = BELTS.belts;
var LIFT_FILL = BELTS["lift fill"];
var BELT_SLOW = BELTS["belt slow"];
var BELT_FAST = BELTS["belt fast"];

/* Tier as value. `items_per_min` is the dump's own figure for the class -- 60, 120, 270, 480,
 * 780 -- so this is a banding of a measurement rather than a parse of "Mk3" out of a display
 * name. An unknown tier draws at the middle tone: the darkest would read as Mk1. */
function beltColour(items_per_min: number | null | undefined): string {
  if (!items_per_min) return BELT_COLOUR;
  if (items_per_min >= 480) return BELT_FAST;
  if (items_per_min >= 270) return BELT_COLOUR;
  return BELT_SLOW;
}

/* How wide a belt is, in metres, and the floor that keeps one visible.
 *
 * Two metres is the game's own belt width, and it is a CONSTANT rather than a field because
 * the save does not carry one: a spline is a centre line, and every tier rides the same frame.
 *
 * The floor is where honesty stops paying: at the whole-world zoom a metre is 0.14 px, so a
 * true-size belt would be a quarter-pixel of nothing. It is ROUTE_MIN_PX and not BELT_MIN_PX
 * because it is not a statement about belts but about the width below which a stroked line
 * stops being drawn at all -- one hairline for both networks, or they fade out at different
 * zooms and the page invents a difference the world does not have. */
var BELT_WIDTH_M = 2;
var BELT_CASING_PX = 2;
var ROUTE_MIN_PX = 1.5;

/* A lift is the same two metres seen end-on, so its ring is that circle: radius half the
 * belt width, floored a little higher than the line is, because a ring has to enclose
 * something to read as a ring rather than as a dot. */
var LIFT_MIN_RADIUS_PX = 2;

/* A splitter or merger with no measured footprint: the docs dump carries clearance for none of
 * these four classes, so the server sends null rather than a number invented there, which
 * would arrive indistinguishable from a measurement. Four metres is the square the pieces snap
 * to, which is also what makes a run read as continuous through one. */
var ATTACHMENT_FALLBACK_M = 4;

/* A route's stroke width, in pixels, from its width in the world. The one place the three
 * network layers agree completely.
 *
 * Exported for power-wires.ts, with the two tables below, so the wires are drawn at their first width
 * by the same expression the zoom pass restyles them with; two copies would be a layer that
 * changed thickness the first time anybody touched the map.
 *
 * THE FLOOR IS A PARAMETER because the answer is not the same for a belt as for a wire: a belt
 * reaches its true width a zoom step or two out, while a wire is 0.2 m and never reaches
 * anything. See ROUTE_FLOOR_PX. Omitted, it is ROUTE_MIN_PX. */
export function routeWeight(width_m: number, ppm: number, floor_px?: number): number {
  return Math.max(floor_px === undefined ? ROUTE_MIN_PX : floor_px, width_m * ppm);
}

function beltWeight(ppm: number): number {
  return routeWeight(BELT_WIDTH_M, ppm);
}

function liftRadius(ppm: number): number {
  return Math.max(LIFT_MIN_RADIUS_PX, (BELT_WIDTH_M / 2) * ppm);
}

/* Does this route go anywhere seen from above? The only evidence left when the dump has no
 * entry for a class.
 *
 * NOT a point count: a lift arrives as TWO points, one at each end of its rise, so counting
 * them says "this is an ordinary run" about the one shape that cannot be drawn as one.
 *
 * A tenth of a metre because that is what the payload is rounded to. */
var FLAT_ROUTE_M = 0.1;

function coversGround(points: Point3M[]): boolean {
  var first = points[0];
  if (!first) return false;
  for (var i = 1; i < points.length; i++) {
    var p = points[i]!;
    if (Math.abs(p[0] - first[0]) >= FLAT_ROUTE_M) return true;
    if (Math.abs(p[1] - first[1]) >= FLAT_ROUTE_M) return true;
  }
  return false;
}

/* What the glyph means, and it has three answers because `lift` has three. `true` and `false`
 * are both measurements, and `false` needs no line -- a belt drawn as a line is what a reader
 * already assumes. `null` is the server declining to guess for a class the dump has no entry
 * for: the page still has to make a drawing decision, so it makes the one the geometry
 * supports and says so, since silence would hand a reader a ring indistinguishable from a
 * measured one. */
function beltKind(b: BeltRow, ring: boolean): string | null {
  if (b.lift === true) return "conveyor lift: vertical, so drawn as a ring";
  if (b.lift === false || b.lift === undefined) return null;
  return ring
    ? "lift or not is unknown for this class: drawn as a ring, since it covers no ground"
    : "lift or not is unknown for this class: drawn as a line, since it covers ground";
}

function beltPopup(b: BeltRow, kind: string | null, first: Point3M, last: Point3M): string {
  return popup([
    titleRow("belt", b.name, b.cls),
    ["kind", kind],
    ["rate", b.items_per_min ? b.items_per_min + " items/min at 100%" : null],
    // Travel order, input to output: the projection reverses the save's own output-first
    // storage, so these two rows mean what they say.
    ["from", first[0] + ", " + first[1] + " m"],
    ["to", last[0] + ", " + last[1] + " m"],
    ["rise", Math.round((last[2] - first[2]) * 10) / 10 + " m"],
    ["chain", "#" + b.chain],
  ]);
}

export function drawBelts(data: BeltsResponse): void {
  // Off by default, like `machines`: 3,085 routes across 7 km is a smear. See reveal() in
  // labels.ts. Immediately over the concrete they run on, and in the order a reader names the
  // networks: belts, then pipes, then power.
  var group = layer("belts", false, BELT_COLOUR, [BAND.built, 10, "belts"]);
  var ppm = pixelsPerMetre();
  var runs: L.Polyline[] = [];
  data.belts.forEach(function (b) {
    var first = b.points_m[0]!;
    var last = b.points_m[b.points_m.length - 1]!;
    var piece: L.Path;
    // An unknown class draws on its own geometry: no horizontal extent means a ring, because a
    // polyline through coincident points draws nothing at all.
    var ring = b.lift === true || (b.lift === null && !coversGround(b.points_m));
    if (ring) {
      piece = L.circleMarker([-first[1], first[0]], {
        radius: liftRadius(ppm),
        color: beltColour(b.items_per_min),
        weight: 1.5,
        fillColor: LIFT_FILL,
        fillOpacity: 0.9,
      });
    } else if (b.points_m.length < 2) {
      return; // a route with one point is not a route, and this is not a lift
    } else {
      piece = routePolyline(b.points_m, b.curve_m, ppm, {
        color: beltColour(b.items_per_min),
        weight: beltWeight(ppm),
        opacity: 0.85,
      });
    }
    // The join the floor filter uses: a belt is keyed by its CHAIN, which is the unit
    // `/api/floors` reasons about. The two ends ride along so a connector's glyph can be put on
    // the end that is actually on the floor being looked at.
    piece._floor = { run: { kind: "belt", key: b.chain }, ends: [first, last] };
    piece.bindPopup(beltPopup(b, beltKind(b, ring), first, last)).addTo(group);
    if (!ring) runs.push(piece as L.Polyline);
  });
  // On a light base mid steel sinks into bare ground, so runs get the power wires' casing; added
  // after every core so sinkRoutes puts it underneath. See power-wires.ts, CASED LINES.
  if (tone() === "light") {
    runs.forEach(function (run) {
      var cased = L.polyline(run.getLatLngs() as L.LatLng[], {
        color: LIFT_FILL,
        weight: beltWeight(ppm) + BELT_CASING_PX,
        opacity: 0.85,
        interactive: false,
      });
      cased._widen = BELT_CASING_PX;
      cased._floor = run._floor;
      var route = run._route;
      if (route) cased._route = { points_m: route.points_m, curve_m: route.curve_m, steps: route.steps.slice() };
      cased.addTo(group);
    });
  }
  (data.attachments || []).forEach(function (a) {
    if (a.x_m === null || a.y_m === null) return;
    var w = (a.w_m || ATTACHMENT_FALLBACK_M) / 2;
    var l = (a.l_m || ATTACHMENT_FALLBACK_M) / 2;
    var junction = L.polygon(footprintCorners(a.x_m, a.y_m, w, l, a.yaw), {
      color: BELT_COLOUR,
      weight: 1,
      fillColor: BELT_COLOUR,
      fillOpacity: 0.85,
    }).bindPopup(
      popup([
        titleRow("belt part", a.name, a.cls),
        ["facing", a.yaw === null || a.yaw === undefined ? null : Math.round(a.yaw) + "°"],
        ["at", a.x_m + ", " + a.y_m + " m"],
        ["id", code(a.instance_leaf)],
      ])
    );
    // A splitter rides in the belts layer but is joined the machines' way -- by instance id, in
    // the band it stands on -- which is the split `/api/floors` makes between runs and
    // placements.
    junction._floor = { id: a.instance_leaf, z_m: a.z_m === null ? undefined : a.z_m };
    junction.addTo(group);
  });
  sinkRoutes();
}

/* Static, and adjacent to the pipes in the wave: a belt changes when the player builds one,
 * not when the game autosaves, and the two share the overlay canvas and the sink pass that
 * decides what a click lands on. */
registerFetch<BeltsResponse>({
  wave: "static",
  rank: 30,
  path: "/api/belts",
  label: "belts",
  clears: ["belts"],
  refilters: true,
  draw: drawBelts,
});

/* The pixels half of the route layers, re-derived whenever the scale changes.
 *
 * A polyline's weight and a circleMarker's radius are the two sizes on this page given in
 * SCREEN pixels, so they are the two that do not follow the map on their own. Everything else
 * here is a polygon in map units and needs none of this.
 *
 * Restyling on zoomend rather than drawing routes as thin polygons in map units, because a
 * polygon cannot have a floor -- below zoom -2 a true two-metre belt is a fraction of a pixel,
 * and the floor is a pixel statement that needs a pixel size to make it in -- and because a
 * 3,085-piece layer of two-metre ribbons would be unclickable at exactly the zooms where the
 * popup is worth opening. The pass costs 0.6 to 0.7 ms median and 3 ms at its worst over 3,933
 * pieces, once per zoom step, against a 16.7 ms frame.
 *
 * A LAYER NOBODY IS LOOKING AT IS SKIPPED, which is most zoomends, since both of these start
 * unticked. That means ticking a layer on at a zoom it was not drawn at HAS to restyle it, or a
 * belt turned on at the factory view arrives at the world view's hairline -- main.ts's
 * `overlayadd` handler is where that happens. */
export function styleRoutes() {
  var ppm = pixelsPerMetre();
  var radius = liftRadius(ppm);
  var alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    var weight = routeWeight(ROUTE_WIDTH_M[name]!, ppm, ROUTE_FLOOR_PX[name]);
    group.eachLayer(function (layer) {
      // Not everything in these groups is drawn geometry: floor mode puts a connector's
      // up/down glyph in the layer its run belongs to, and that is a marker with an icon and
      // no stroke to size. The test is what a piece IS rather than what it is not.
      if (!(layer instanceof L.Path)) return;
      var piece = layer as L.Path & { setRadius?: (r: number) => void };
      // Four kinds of piece share these layers and only two are sized in pixels: a lift's ring
      // by its radius, a run by its weight. A splitter is a polygon in map units and is already
      // right at every zoom. A chevron is map units too, so what changes for it is whether it
      // is drawn at all -- the one piece here with a zoom below which it is noise.
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      // A round glyph, with two answers. A lift's ring is a two-metre belt seen end-on and is
      // sized from the scale; a power pole's disc is a MARK at a fixed pixel radius and is left
      // as created. `_fixed` is the piece saying which, rather than this pass guessing from a
      // layer name.
      else if (piece.setRadius) {
        if (!piece._fixed) piece.setRadius(radius);
      } else if (!(piece instanceof L.Polygon)) {
        // `_widen` is a casing's fixed rim in SCREEN pixels around a line whose own width
        // follows the scale, so the two have to be recombined at every zoom or the rim grows
        // with the map and stops being a rim. Only power-wires.ts sets it.
        piece.setStyle({ weight: weight + (piece._widen || 0) });
        // And the geometry, not just the stroke: the zoom that changes the width is the zoom
        // that changes how many pieces a bend is worth.
        retessellate(piece as L.Polyline, ppm);
      }
    });
  });
}

/* The fluid network, drawn the same way and told apart by colour.
 *
 * The same posture as the belts next door -- one colour family, tier as a VALUE step inside it,
 * width physical -- because a page that encodes two networks by two different grammars makes
 * the reader learn twice.
 *
 * NO GLYPH, and that is a measurement rather than an omission: there is no vertical pipe piece,
 * the tightest of the reference world's 503 spanning 11.6 cm horizontally. The one-point guard
 * below stays, because "none on this world" is not "none ever".
 *
 * ARROWS WHERE THE DIRECTION IS INFERRED, and nothing at all where it is not. The save does not
 * record a pipe's flow direction, but it does record the plumbing graph and TYPE a machine's
 * ports, so `/api/pipes` infers a direction where the network admits only one and says
 * `unknown` where it admits two. The popup names which of the two a reader is looking at.
 */
/* Oxide. A pipe's obvious colour is a saturated amber (#d99a3e), which is the pending
 * removal's own family, and the mid-rust band is the bauxite dot's own neighbourhood -- pipes run exactly
 * where bauxite is refined -- so the network went darker instead of brighter. Still warm where
 * the belts are cool. Measured against the current full table: dE 27.4 from the bauxite dot,
 * 28.7 from the stopped red, 30.1 from the nearest ground (Red Bamboo Fields), 49.0 from the
 * nearest of the artwork tones binned in power-wires.ts, and 102.1 from the extractor ultramarine. */
var PIPE_COLOUR = declareColours("routes", { pipes: "#7d221a" }).pipes;

/* The two tier tones, one step of value either side of PIPE_COLOUR -- which stays the middle
 * one, so the swatch in the layer control is still the network's own colour. The step is the
 * belts' step, dE 15.1 between Mk1 and Mk2 against the belts' 15.6.
 *
 * A ramp can walk a colour into a neighbour, so both ends are measured: Mk2, the lighter end
 * and the closest the family comes to the bauxite dot, stays dE 21.4 from it and 23.4 from the
 * stopped red; Mk1's nearest is Red Bamboo Fields at 32.6, with the bauxite dot at 33.5. */
var PIPE_TIER = declareColours("routes", { "pipe mk1": "#690e06", "pipe mk2": "#91362e" });
var PIPE_MK1 = PIPE_TIER["pipe mk1"];
var PIPE_MK2 = PIPE_TIER["pipe mk2"];

/* Tier as value, the belts' banding: `flow_m3_min` is the dump's own figure for the class --
 * 300 on Mk1, 600 on Mk2 -- so this bands a measurement rather than parsing "MK2" out of a
 * display name. A Pipeline Mk.2 is the same 1.3 m bore with a better pump rating, so drawing
 * it wider would contradict the map's own scale bar. Two tiers, so two tones and the middle for
 * the unknown: the darker would read as Mk1. */
function pipeColour(flow_m3_min: number | null | undefined): string {
  if (!flow_m3_min) return PIPE_COLOUR;
  return flow_m3_min >= 600 ? PIPE_MK2 : PIPE_MK1;
}

/* A pipe is 1.3 m across, and like the belt's two metres it is a CONSTANT rather than a field:
 * the save carries a centre line and no bore, and both tiers ride the same frame. Narrower than
 * a belt, so it reaches ROUTE_MIN_PX about a zoom step sooner. */
var PIPE_WIDTH_M = 1.3;

function pipeWeight(ppm: number): number {
  return routeWeight(PIPE_WIDTH_M, ppm);
}

/* What the popup says about a direction, keyed by what the server based it on. The `from` and
 * `to` rows carry the direction itself, so this row only has to carry the WARRANT.
 *
 * EXHAUSTIVE, which is what `Record<PipeFlowBasis, …>` buys over `Record<string, …>`: a fifth
 * basis in `domain/world/flow.py` is a missing key here and a compile error, rather than a
 * basis nobody thought about falling through a default and being printed as a claim about the
 * network. `unresolved` is a real key for the pipes the network does not settle -- one in a
 * loop, or a trunk with producers and consumers on both sides. */
var PIPE_FLOW_UNKNOWN = "not recorded, and the network does not imply it";

var PIPE_FLOW_BASIS: Record<PipeFlowBasis, string> = {
  "machine port": "→ a typed machine port at one end",
  pump: "→ pump orientation",
  propagated: "→ inferred from the network",
  unresolved: PIPE_FLOW_UNKNOWN,
};

function pipePopup(p: PipeRow, first: Point3M, last: Point3M): string {
  var known = p.direction === "forward" || p.direction === "reverse";
  var head = p.direction === "reverse" ? last : first;
  var tail = p.direction === "reverse" ? first : last;
  return popup([
    titleRow("pipe", p.name, p.cls),
    // Off the game's own FGPipeNetwork rather than from what the pipe is plugged into, which is
    // why it can be stated flatly.
    ["fluid", p.fluid_name],
    ["capacity", p.flow_m3_min ? p.flow_m3_min + " m³/min at 100%" : null],
    ["flow", known ? PIPE_FLOW_BASIS[p.basis] : PIPE_FLOW_UNKNOWN],
    // `from`/`to` where the direction is known, which is the belt popup's own wording and means
    // the same thing there; `ends` where it is not, so the two are never confused.
    ["from", known ? head[0] + ", " + head[1] + " m" : null],
    ["to", known ? tail[0] + ", " + tail[1] + " m" : null],
    ["ends", known ? null : first[0] + ", " + first[1] + " m and " + last[0] + ", " + last[1] + " m"],
    // Signed only where there is a direction to sign it against.
    [
      "rise",
      known
        ? Math.round((tail[2] - head[2]) * 10) / 10 + " m"
        : Math.abs(Math.round((last[2] - first[2]) * 10) / 10) + " m",
    ],
    ["network", p.network === null ? null : "#" + p.network],
  ]);
}

/* Chevrons: the direction, drawn.
 *
 * Geometry in METRES, unlike the line it sits on, so it scales with the map and stays the same
 * size relative to the plumbing at every zoom. Three metres long and 2.4 across -- a little
 * under twice the 1.3 m bore, which is what makes it read as a mark ON the pipe rather than as
 * a kink IN it.
 *
 * Placed by ARC LENGTH and not per corner: one every 24 m with a minimum of one per pipe, so a
 * run reads as a dotted line of them rather than a cluster at every bend. Counted in the PLAN,
 * because a pipe's climb is not length it has anywhere to put a mark. The 4 m floor drops the
 * marks that would be three quarters as long as the piece carrying them.
 *
 * HIDDEN AT WORLD ZOOM, by the same grammar the hairline floor uses: below 5 px a chevron has
 * no discernible apex and is a dash, which says nothing about direction. Three metres reaches
 * 5 px at zoom 1, which is labels.ts' FACTORY_MAX_ZOOM.
 *
 * Not interactive: a mark that stole its own pipe's popup would make the direction unreadable
 * by making the piece unclickable. */
var CHEVRON_LENGTH_M = 3;
var CHEVRON_SPAN_M = 2.4;
var CHEVRON_SPACING_M = 24;
var CHEVRON_MIN_RUN_M = 4;
var CHEVRON_MIN_PX = 5;
var CHEVRON_WEIGHT_PX = 1.5;

/* A value step far above all three pipe tones, so it reads against the line it is drawn on: at
 * its 0.7 opacity the composite over those tones is dE 41.3 to 50.6 from the pipe underneath.
 * The nearest colour anywhere else on the page is the iron-ore dot, at dE 14.3 to 17.0 from
 * those composites -- a filled disc on terrain rather than a thin V on a line, and the
 * discharge in palette.ts carries that measurement. The extractor ultramarine is dE 119.2 away. */
var CHEVRON_COLOUR = declareColours("routes", { chevrons: "#e8cbb4" }).chevrons;
var CHEVRON_OPACITY = 0.7;

function chevronOpacity(ppm: number): number {
  return CHEVRON_LENGTH_M * ppm >= CHEVRON_MIN_PX ? CHEVRON_OPACITY : 0;
}

/* Where the chevrons go on one route, in world metres, as [[x, y], [x, y], [x, y]] apexes.
 * Takes a polyline and a flag rather than a pipe, so a belt could be marked by it too; see
 * ROUTE_CHEVRONS. */
/** One straight leg of a route, with where along the whole route it starts. */
interface Run {
  x: number;
  y: number;
  ux: number;
  uy: number;
  d: number;
  at: number;
}

function routeChevrons(points_m: Point3M[], reverse: boolean): PointM[][] {
  var pts = reverse ? points_m.slice().reverse() : points_m;
  var runs: Run[] = [];
  var total = 0;
  for (var i = 1; i < pts.length; i++) {
    var dx = pts[i]![0] - pts[i - 1]![0];
    var dy = pts[i]![1] - pts[i - 1]![1];
    var d = Math.sqrt(dx * dx + dy * dy);
    if (!(d > 0)) continue;
    runs.push({ x: pts[i - 1]![0], y: pts[i - 1]![1], ux: dx / d, uy: dy / d, d: d, at: total });
    total += d;
  }
  if (!runs.length || total < CHEVRON_MIN_RUN_M) return [];
  var marks: PointM[][] = [];
  var n = Math.max(1, Math.floor(total / CHEVRON_SPACING_M));
  for (var k = 0; k < n; k++) {
    var along = ((k + 0.5) / n) * total;
    var run = runs[runs.length - 1]!;
    for (var j = 0; j < runs.length; j++) {
      if (along <= runs[j]!.at + runs[j]!.d) {
        run = runs[j]!;
        break;
      }
    }
    var t = along - run.at;
    var tx = run.x + run.ux * (t + CHEVRON_LENGTH_M / 2);
    var ty = run.y + run.uy * (t + CHEVRON_LENGTH_M / 2);
    var bx = tx - run.ux * CHEVRON_LENGTH_M;
    var by = ty - run.uy * CHEVRON_LENGTH_M;
    var nx = -run.uy * (CHEVRON_SPAN_M / 2);
    var ny = run.ux * (CHEVRON_SPAN_M / 2);
    marks.push([
      [bx + nx, by + ny],
      [tx, ty],
      [bx - nx, by - ny],
    ]);
  }
  return marks;
}

export function drawPipes(data: PipesResponse): void {
  // Off by default, like `belts` and `machines`; see reveal() in labels.ts. Directly under the
  // belts, which is the pair they are.
  var group = layer("pipes", false, PIPE_COLOUR, [BAND.built, 20, "pipes"]);
  var ppm = pixelsPerMetre();
  var alpha = chevronOpacity(ppm);
  data.pipes.forEach(function (p) {
    if (p.points_m.length < 2) return; // a route with one point is not a route
    var first = p.points_m[0]!;
    var last = p.points_m[p.points_m.length - 1]!;
    var run = routePolyline(p.points_m, p.curve_m, ppm, {
      color: pipeColour(p.flow_m3_min),
      weight: pipeWeight(ppm),
      opacity: 0.85,
    }).bindPopup(pipePopup(p, first, last));
    // `row`, not this list's index: it is the pipe's position in the RAW table, which is
    // what `/api/floors` keys a pipe run by and what stays right when a row is torn.
    run._floor = { run: { kind: "pipe", key: p.row }, ends: [first, last] };
    run.addTo(group);
    if (!ROUTE_CHEVRONS.pipes) return;
    if (p.direction !== "forward" && p.direction !== "reverse") return;
    routeChevrons(p.points_m, p.direction === "reverse").forEach(function (mark) {
      var piece = L.polyline(
        mark.map(function (q): L.LatLngTuple {
          return [-q[1], q[0]];
        }),
        {
          color: CHEVRON_COLOUR,
          weight: CHEVRON_WEIGHT_PX,
          opacity: alpha,
          interactive: false,
        }
      );
      piece._chevron = true;
      // Its pipe's key and NO ends, so the floor filter keeps a mark exactly when it keeps the
      // pipe under it and never mistakes the mark for a run whose end could carry a connector.
      piece._floor = { run: { kind: "pipe", key: p.row } };
      piece.addTo(group);
    });
  });
  sinkRoutes();
}

/** The belts' twin; see the note on that registration for why both are static. */
registerFetch<PipesResponse>({
  wave: "static",
  rank: 40,
  path: "/api/pipes",
  label: "pipes",
  clears: ["pipes"],
  refilters: true,
  draw: drawPipes,
});

/* The route layers share the overlay canvas with the machines and the node dots, and
 * hit-testing there is draw order with the LAST match winning. A belt run crosses every machine
 * it feeds, and a polyline's hit area is its width plus Leaflet's click tolerance, so a route
 * layer added after the machines would quietly take the click on every machine it passes over.
 * Pushed to the back instead: under the machines, under the node dots, over the foundations
 * (a separate pane, so a separate canvas).
 *
 * THEIR OWN PANE WOULD NOT WORK: the DOM delivers a click to the topmost element under the
 * pointer and the overlay pane's canvas covers the entire viewport, so a clickable layer below
 * it is not clickable at all. `bringToBack` is a no-op on a path whose group is not on the map,
 * so this is safe to call whenever.
 *
 * ORDER INSIDE A LAYER is decided by the order of the calls: each `bringToBack` puts its caller
 * below everything already sunk, so the piece sunk LAST ends up at the very bottom. The
 * junction squares go first and the runs after them, which leaves a splitter sitting ON the
 * lines it joins -- a square hidden beneath a line is not CLICKABLE, and the popup naming the
 * piece is the whole reason it has one.
 *
 * A POWER POLE IS THE SAME CASE AS A SPLITTER, which is what `_fixed` buys here beyond the
 * restyle above: a pole sunk with the runs would be a mark under every line it terminates and a
 * popup nobody can open. Partitioned on the mark rather than on draw order, because draw order
 * is power-wires.ts's business and this rule is not.
 */
export var ROUTE_LAYERS = ["belts", "pipes", "power"];

/* What each route layer is worth in metres. The one place the three differ, so the one place
 * the shared passes above have to look. A power wire's 0.2 m is the thinnest thing on this map
 * and reaches ROUTE_MIN_PX two zoom steps before a pipe does. */
export var ROUTE_WIDTH_M: Record<string, number> = {
  belts: BELT_WIDTH_M,
  pipes: PIPE_WIDTH_M,
  power: 0.2,
};

/* Where each layer's width stops being allowed to fall, for the layers whose answer is not
 * ROUTE_MIN_PX. A layer with no entry gets routeWeight's default, so this table lists only the
 * exceptions.
 *
 * ROUTE_MIN_PX is 1.5 because that is where a stroked line stops being drawn at all, which is
 * the right floor for a network you turn on to look AT. The wires are the one layer that is on
 * at the whole-world view, where 1.5 px over the game's own artwork is barely visible.
 *
 * 2.5 px is the whole width rule for that layer rather than a lower bound on one: 0.2 m reaches
 * 2.5 px at 12.5 px to the metre and this map's maxZoom is worth 8.96, so a wire is at its
 * floor at EVERY zoom the page has. That puts a wire in the same grammar as the pole at its
 * end -- a MARK, sized to be seen, not a footprint. See POLE_RADIUS_PX in power-wires.ts. */
export var ROUTE_FLOOR_PX: Record<string, number> = {
  power: 2.5,
};

/* Which route layers carry direction chevrons. A BELT HAS A DIRECTION TOO -- its points are in
 * travel order -- and `routeChevrons` takes a polyline and a flag precisely so that turning
 * this to `true` is the whole of the work. It stays `false` because 3,085 belt runs would put
 * some 2,500 more marks on the same canvas as the pipes' 412. */
var ROUTE_CHEVRONS: Record<string, boolean> = { belts: false, pipes: true };

export function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var chevrons: L.Path[] = [];
    var glyphs: L.Path[] = [];
    var runs: L.Path[] = [];
    group.eachLayer(function (layer) {
      // Paths only: a floor connector's glyph is a marker in the marker pane, above this
      // canvas and no part of the stacking question this pass answers.
      if (!(layer instanceof L.Path)) return;
      var piece = layer as L.Path;
      (piece._chevron
        ? chevrons
        : piece instanceof L.Polygon || piece._fixed
          ? glyphs
          : runs
      ).push(piece);
    });
    // Sunk FIRST is left highest, per the note above, so the chevrons go before the glyphs
    // and the runs: a direction mark under the line it marks would not be a mark.
    chevrons
      .concat(glyphs)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}
