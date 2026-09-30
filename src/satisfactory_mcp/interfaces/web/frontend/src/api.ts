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

var UNPINNED = ["/api/summary", "/api/worlds"];

var staleListeners: Array<() => void> = [];

function tokened(url: string): string {
  var bare = url.split("?")[0]!;
  if (!state.token || UNPINNED.indexOf(bare) >= 0) return url;
  return url + (url.indexOf("?") < 0 ? "?" : "&") + "as_of=" + encodeURIComponent(state.token);
}

function stale(on: boolean): void {
  if (state.stale === on) return;
  state.stale = on;
  staleListeners.forEach(function (listener) {
    listener();
  });
}

export function onStale(listener: () => void): void {
  staleListeners.push(listener);
}

var tokenListeners: Array<() => void> = [];

export function onToken(listener: () => void): void {
  tokenListeners.push(listener);
}

export function holdToken(token: string): void {
  var moved = state.token !== token;
  state.token = token;
  stale(false);
  if (moved)
    tokenListeners.forEach(function (listener) {
      listener();
    });
}

export function dropToken(): void {
  state.token = "";
  stale(false);
}

function answer<T extends ApiError>(path: string, r: Response, sent?: string): Promise<T> {
  return r.json().then(function (body: T & { stale?: boolean }) {
    if (r.status === 409 && body.stale && sent && sent === state.token) stale(true);
    if (!r.ok || body.error) {
      var error: StatusError = new Error(body.error || r.status + " " + path);
      error.status = r.status;
      error.body = body;
      throw error;
    }
    return body;
  });
}

function filled(path: string, subject?: string): string {
  return subject === undefined ? path : path.replace(/\{[^}]+\}/, encodeURIComponent(subject));
}

function request(method: string, body?: object): RequestInit {
  var init: RequestInit = { method: method };
  if (body) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return init;
}

export function get<T extends ApiError>(path: ApiUrl, subject?: string): Promise<T> {
  var url = filled(path, subject);
  var sent = state.token;
  return fetch(tokened(pinned(url))).then(function (r) {
    return answer<T>(url, r, sent);
  });
}

export function send<T extends ApiError>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: ApiPath,
  body?: object,
  subject?: string,
  query?: string
): Promise<T> {
  var url = filled(path, subject);
  if (query) url += "?" + query;
  return fetch(pinned(url), request(method, body)).then(function (r) {
    return answer<T>(url, r);
  });
}

export interface Ticket {
  fresh(): boolean;
}

var issued: Record<string, number> = {};

export function latest(slot: string): Ticket {
  var serial = (issued[slot] || 0) + 1;
  var epoch = state.epoch;
  issued[slot] = serial;
  return {
    fresh: function () {
      return issued[slot] === serial && state.epoch === epoch;
    },
  };
}

export interface StatusError extends Error {
  status?: number;
  body?: ApiError;
}

export function missing(reason: unknown): boolean {
  return (reason as StatusError | null | undefined)?.status === 404;
}

export type Pushed<T, C> = { conflict: false; body: T } | { conflict: true; body: C };

export function push<T extends ApiError, C extends ApiError>(path: ApiPath, body: object, subject?: string): Promise<Pushed<T, C>> {
  var url = filled(path, subject);
  return fetch(pinned(url), request("POST", body)).then(function (r) {
    return r
      .json()
      .catch(function () {
        return {};
      })
      .then(function (payload: ApiError): Pushed<T, C> {
        if (r.status === 409) return { conflict: true, body: payload as C };
        if (!r.ok || payload.error) {
          var error: StatusError = new Error(payload.error || r.status + " " + url);
          error.status = r.status;
          throw error;
        }
        return { conflict: false, body: payload as T };
      });
  });
}
