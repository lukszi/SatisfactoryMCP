/* The page's own words for the things it draws with: points, boxes, and a drawn route's shape.
 *
 * Not payload types. The server describes the same structures inside its payloads, but
 * `hermite()` takes a POINT rather than a response and `RouteShape` is a thing the page builds
 * and hangs on a polyline. This module imports nothing and is types all the way down, so the
 * transpiler erases the file whole and it is in no bundle.
 */

/** A point in game metres, `[x, y]`. Latitude is `-y`; see `xy` in map.ts. */
export type PointM = [number, number];

/** A point with its height, `[x, y, z]` -- what the route splines carry. */
export type Point3M = [number, number, number];

/** `[x_min, y_min, x_max, y_max]`, game axes. The y ends swap on the way to Leaflet. */
export type BboxM = [number, number, number, number];

/** The tangents that bend one span of a route, `[leave, arrive]` in game metres.
 *
 * `leave` leaves the point behind the span and `arrive` arrives at the point ahead of it,
 * which is the pair a cubic Hermite between those two points takes. They are displacements in
 * the same space as `points_m`, so whatever transform a client applies to a point applies to
 * these unchanged -- see hermite() in routes.ts. */
export type SpanCurveM = [Point3M, Point3M];

/** A route's curve, one entry per span, in step with `points_m`.
 *
 * `null` in a slot means that span is straight and is drawn as the line it already was.
 * `null` for the whole field means the route has no bend anywhere in it, or the projection
 * predates schema 15 -- both of which mean the same thing to a client. */
export type RouteCurveM = (SpanCurveM | null)[] | null;

/** What a drawn route was built from: enough to draw it again at a different scale.
 *
 * Hung on the polyline itself, because tessellation is not reversible -- the latlngs on a
 * drawn curve are already subdivided, and subdividing those again would converge on the
 * approximation rather than on the spline. */
export interface RouteShape {
  points_m: Point3M[];
  curve_m: RouteCurveM;
  /** How many pieces each span was last cut into, so an unchanged zoom step does no work. */
  steps: number[];
}
