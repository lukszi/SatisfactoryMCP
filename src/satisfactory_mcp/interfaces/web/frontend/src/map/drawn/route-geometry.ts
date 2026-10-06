/* The geometry of a route, belt or pipe: how a spline becomes a polyline, and where the
 * direction chevrons go along it.
 *
 * A belt or pipe spline stores a tangent either side of every control point, so a run the
 * player laid as an arc is an arc, and a chord through its corners can be metres out.
 *
 * THE SUBDIVISION IS ZOOM-DEPENDENT, which is why it is affordable: asking "how far is this
 * curve from its chord IN PIXELS, right now" adds no points at the whole-world view, where every
 * route is on screen at once, and only a few at maxZoom.
 *
 * A SPAN WITH NO CURVE IS NEVER TOUCHED: the server sends null for a straight span and for a
 * route with no bend anywhere in it, so a straight run is the same two points at every zoom.
 */

import { L } from "../leaflet";
import { latLngOf } from "../map";

import type { Point3M, PointM, RouteCurveM, SpanCurveM } from "../geometry";

/* Half a pixel: below this a bend and the line through it land on the same pixels, and the
 * canvas draws no finer than that anyway. */
var CURVE_TOLERANCE_PX = 0.5;

/* A ceiling, so that the bound on the work does not come from the data. Generous, because
 * almost every span is nearly flat and never comes close to it. */
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
  const ax = p0[0];
  const ay = p0[1];
  const vx = p1[0] - ax;
  const vy = p1[1] - ay;
  const chord = Math.sqrt(vx * vx + vy * vy);
  const b1x = ax + span[0][0] / 3;
  const b1y = ay + span[0][1] / 3;
  const b2x = p1[0] - span[1][0] / 3;
  const b2y = p1[1] - span[1][1] / 3;
  if (!(chord > 0)) {
    // Coincident ends -- the joint where a lift meets its belt. There is no chord to measure
    // against, so the control points' own offset is the whole of the departure.
    const d1 = Math.hypot(b1x - ax, b1y - ay);
    const d2 = Math.hypot(b2x - ax, b2y - ay);
    return 0.75 * Math.max(d1, d2);
  }
  const off1 = Math.abs((b1x - ax) * vy - (b1y - ay) * vx) / chord;
  const off2 = Math.abs((b2x - ax) * vy - (b2y - ay) * vx) / chord;
  return 0.75 * Math.max(off1, off2);
}

/* How many straight pieces one span is worth at this scale. 1 means "draw the chord".
 *
 * A cubic subdivided into n uniform pieces has an error of about `flatness / n^2`, so the n
 * that puts that under the tolerance is the square root of the ratio -- which is why a curve
 * ten times bigger costs three times the points and not ten. */
function spanSteps(flat_m: number, ppm: number): number {
  const px = flat_m * ppm;
  if (!(px > CURVE_TOLERANCE_PX)) return 1;
  return Math.min(CURVE_MAX_STEPS, Math.ceil(Math.sqrt(px / CURVE_TOLERANCE_PX)));
}

/* One point along a Hermite span, in game metres. The tangents arrive in the same space and
 * units as the points -- which is what `/api/belts` promises about `curve_m` -- so this is the
 * plain basis, and the y-flip below can be applied to the RESULT rather than to the inputs. */
function hermite(p0: Point3M, p1: Point3M, span: SpanCurveM, t: number): PointM {
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;
  return [
    h00 * p0[0] + h10 * span[0][0] + h01 * p1[0] + h11 * span[1][0],
    h00 * p0[1] + h10 * span[0][1] + h01 * p1[1] + h11 * span[1][1],
  ];
}

/* A route as the latlngs Leaflet draws, tessellated for the scale given, plus the step counts.
 * The curve is computed in game metres and only its result is plotted. */
function routeLatLngs(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number
): { latlngs: L.LatLngTuple[]; steps: number[] } {
  const latlngs: L.LatLngTuple[] = [latLngOf(points_m[0]!)];
  const steps: number[] = [];
  for (let i = 0; i < points_m.length - 1; i++) {
    const a = points_m[i]!;
    const b = points_m[i + 1]!;
    const span = curve_m ? curve_m[i] : null;
    const n = span ? spanSteps(spanFlatnessM(a, b, span), ppm) : 1;
    steps.push(n);
    for (let k = 1; k < n; k++) {
      latlngs.push(latLngOf(hermite(a, b, span!, k / n)));
    }
    latlngs.push(latLngOf(b));
  }
  return { latlngs: latlngs, steps: steps };
}

/* One route as a drawn polyline, carrying the spline it was tessellated from. Re-tessellating
 * at a new zoom needs the SOURCE, and the drawn latlngs are not it -- they are already an
 * approximation, and subdividing them again would converge on that approximation rather than
 * on the curve. A route with no curve still gets a `_route`, at no cost: `steps` comes back all
 * ones and the zoom pass never touches it again. */
export function routePolyline(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number,
  options: L.PolylineOptions
): L.Polyline {
  const shape = routeLatLngs(points_m, curve_m, ppm);
  const piece = L.polyline(shape.latlngs, options);
  piece._route = { points_m: points_m, curve_m: curve_m, steps: shape.steps };
  return piece;
}

/* Redraw one route for a new scale, and say whether it actually moved. Guarded on the step
 * counts rather than on the zoom, because most pieces do not change at most zoom steps: a run
 * with no bend never changes at all, and a gentle one holds the same subdivision across several
 * steps. */
export function retessellate(piece: L.Polyline, ppm: number): boolean {
  const route = piece._route;
  if (!route || !route.curve_m) return false;
  const shape = routeLatLngs(route.points_m, route.curve_m, ppm);
  let same = shape.steps.length === route.steps.length;
  for (let i = 0; same && i < shape.steps.length; i++) same = shape.steps[i] === route.steps[i];
  if (same) return false;
  route.steps = shape.steps;
  piece.setLatLngs(shape.latlngs);
  return true;
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

var CHEVRON_OPACITY = 0.7;

export function chevronOpacity(ppm: number): number {
  return CHEVRON_LENGTH_M * ppm >= CHEVRON_MIN_PX ? CHEVRON_OPACITY : 0;
}

/** One straight leg of a route: where it starts, its unit direction, its length, and how far
 *  along the whole route it begins. */
interface Leg {
  startX: number;
  startY: number;
  dirX: number;
  dirY: number;
  length: number;
  offset: number;
}

/** Where the chevrons go on one route, in world metres, as [[x, y], [x, y], [x, y]] apexes. */
export function routeChevrons(points_m: Point3M[], reverse: boolean): PointM[][] {
  const points = reverse ? points_m.slice().reverse() : points_m;
  const legs: Leg[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const dx = points[i]![0] - points[i - 1]![0];
    const dy = points[i]![1] - points[i - 1]![1];
    const length = Math.sqrt(dx * dx + dy * dy);
    if (!(length > 0)) continue;
    legs.push({
      startX: points[i - 1]![0],
      startY: points[i - 1]![1],
      dirX: dx / length,
      dirY: dy / length,
      length: length,
      offset: total,
    });
    total += length;
  }
  if (!legs.length || total < CHEVRON_MIN_RUN_M) return [];
  const marks: PointM[][] = [];
  const count = Math.max(1, Math.floor(total / CHEVRON_SPACING_M));
  for (let k = 0; k < count; k++) {
    const along = ((k + 0.5) / count) * total;
    let leg = legs[legs.length - 1]!;
    for (let j = 0; j < legs.length; j++) {
      if (along <= legs[j]!.offset + legs[j]!.length) {
        leg = legs[j]!;
        break;
      }
    }
    const intoLeg = along - leg.offset;
    const tipX = leg.startX + leg.dirX * (intoLeg + CHEVRON_LENGTH_M / 2);
    const tipY = leg.startY + leg.dirY * (intoLeg + CHEVRON_LENGTH_M / 2);
    const baseX = tipX - leg.dirX * CHEVRON_LENGTH_M;
    const baseY = tipY - leg.dirY * CHEVRON_LENGTH_M;
    const sideX = -leg.dirY * (CHEVRON_SPAN_M / 2);
    const sideY = leg.dirX * (CHEVRON_SPAN_M / 2);
    marks.push([
      [baseX + sideX, baseY + sideY],
      [tipX, tipY],
      [baseX - sideX, baseY - sideY],
    ]);
  }
  return marks;
}
