/* The fetch layer: one function, and the two query parameters every endpoint takes.
 *
 * `world` and `save` are not per-call arguments -- they are the page's current selection, so
 * a caller that spelled them itself could drift from the picker. `get` is generic over the
 * response and the caller supplies the type, which comes from api-shapes.ts; the PATH comes
 * from the server's own generated schema, so a typo in a URL is a compile error rather than a
 * toast at runtime. Requests that read headers, carry no world, or are made by the browser
 * as images do not come through here.
 */

import type { paths } from "./api-schema";
import { state } from "./state";

/** Every path the server serves, straight out of its own OpenAPI document. */
export type ApiPath = keyof paths;

/* The error branch any reply may carry instead of its payload, and the one shape on this page
 * the server does not describe. FastAPI publishes no schema for the `{"error": "..."}` a 4xx
 * carries, because a handler that fails returns a JSONResponse, which SKIPS its own response
 * model -- so the document has no way to know the branch exists. It is the frontend's claim
 * about the whole surface and lives here, in the module that turns it into a throw, rather
 * than in api-shapes.ts where everything else resolves to a generated component.
 *
 * Its members are all optional: `get` is generic over `T extends ApiError`, and a body type
 * with no `error` field at all would not be assignable to a required one. */
export interface ApiError {
  error?: string;
}

/* The base layers `/api/maptiles/{layer}/…` serves, as the frontend's claim.
 *
 * Hand-written because the generated schema cannot supply it: `layer` is a plain `str` path
 * parameter, so `api-schema.d.ts` types it `string`, and the names live in `MAP_LAYERS` in
 * routers/tiles.py, which the document never sees. The check is therefore by construction --
 * `tilePath` is the only place a tile URL is built and `ModeSpec.layer` in tiles.ts is typed
 * by this union, so a mode naming a layer this server does not serve is a compile error.
 * test_architecture.py pins the union against `MAP_LAYERS` itself. */
export type MapTileLayer = "map" | "terrain" | "satellite";

/* One tile's path, or the template Leaflet fills in. The coordinates are `string | number`
 * so that both callers go through here: the probe asks for `0, 0, 0` and the TileLayer asks
 * for `"{z}", "{x}", "{y}"`. */
export function tilePath(
  layer: MapTileLayer,
  z: string | number,
  x: string | number,
  y: string | number
): string {
  return "/api/maptiles/" + layer + "/" + z + "/" + x + "/" + y;
}

/* A path plus a query string, which is what the two callers that take one pass -- spelled
 * inline at the call site, so the template keeps the path half checked.
 *
 * Exported because the fetch registry stores paths rather than calls: a `Fetcher` names the
 * URL it wants and load.ts passes it to `get`, so the type has to travel with it or the
 * compile-time check on every registered path is lost. See registry.ts. */
export type ApiUrl = ApiPath | `${ApiPath}?${string}`;

function pinned(path: string): string {
  var q = "";
  var sep = path.indexOf("?") < 0 ? "?" : "&";
  if (state.world) {
    q += sep + "world=" + encodeURIComponent(state.world);
    sep = "&";
  }
  if (state.save) {
    q += sep + "save=" + encodeURIComponent(state.save);
  }
  return path + q;
}

function answer<T extends ApiError>(path: string, r: Response): Promise<T> {
  return r.json().then(function (body: T) {
    if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
    return body;
  });
}

export function get<T extends ApiError>(path: ApiUrl): Promise<T> {
  return fetch(pinned(path)).then(function (r) {
    return answer<T>(path, r);
  });
}

export function send<T extends ApiError>(
  method: "POST" | "PATCH" | "DELETE",
  path: ApiPath,
  body?: object,
  subject?: string,
  query?: string
): Promise<T> {
  var url = subject === undefined ? path : path.replace(/\{[^}]+\}/, encodeURIComponent(subject));
  if (query) url += "?" + query;
  var init: RequestInit = { method: method };
  if (body) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return fetch(pinned(url), init).then(function (r) {
    return answer<T>(url, r);
  });
}
