/* The fetch layer: one function, and the two query parameters every endpoint takes.
 *
 * `world` and `save` are not per-call arguments -- they are the page's current selection, so
 * a caller that spelled them itself could drift from the picker. `get` is generic over the
 * response and the caller supplies the type, which comes from api/shapes.ts; the PATH comes
 * from the server's own generated schema, so a typo in a URL is a compile error rather than a
 * toast at runtime. Requests that read headers, carry no world, or are made by the browser
 * as images do not come through here.
 */

import type { paths } from "./schema";
import { createListeners } from "../app/listeners";
import { state } from "../app/state";

/** Every path the server serves, straight out of its own OpenAPI document. */
export type ApiPath = keyof paths;

/* The error branch any reply may carry instead of its payload, and the one shape on this page
 * the server does not describe. FastAPI publishes no schema for the `{"error": "..."}` a 4xx
 * carries, because a handler that fails returns a JSONResponse, which SKIPS its own response
 * model -- so the document has no way to know the branch exists. It is the frontend's claim
 * about the whole surface and lives here, in the module that turns it into a throw, rather
 * than in api/shapes.ts where everything else resolves to a generated component.
 *
 * Its members are all optional: `get` is generic over `T extends ApiError`, and a body type
 * with no `error` field at all would not be assignable to a required one. */
export interface ApiError {
  error?: string;
}

/* A base layer `/api/maptiles/{layer}/…` serves: a map type id out of `/api/maps`. The ids
 * are data, so the compiler cannot check one; the server answers an unknown id with a 404
 * that lists the ones it has. */
export type MapTileLayer = string;

/* One tile's path, or the template Leaflet fills in. The coordinates are `string | number`
 * so that both callers go through here: the probe asks for `0, 0, 0` and the TileLayer asks
 * for `"{z}", "{x}", "{y}"`. */
export function tilePath(
  layer: MapTileLayer,
  z: string | number,
  x: string | number,
  y: string | number
): string {
  return "/api/maptiles/" + encodeURIComponent(layer) + "/" + z + "/" + x + "/" + y;
}

/* A path plus a query string, which is what the two callers that take one pass -- spelled
 * inline at the call site, so the template keeps the path half checked.
 *
 * Exported because the fetch registry stores paths rather than calls: a `Fetcher` names the
 * URL it wants and load.ts passes it to `get`, so the type has to travel with it or the
 * compile-time check on every registered path is lost. See registry.ts. */
export type ApiUrl = ApiPath | `${ApiPath}?${string}`;

function withSelection(path: string): string {
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

var TOKENLESS_PATHS = ["/api/summary", "/api/worlds"];

var staleChanged = createListeners();

function withSaveToken(url: string): string {
  var bare = url.split("?")[0]!;
  if (!state.saveToken || TOKENLESS_PATHS.indexOf(bare) >= 0) return url;
  return url + (url.indexOf("?") < 0 ? "?" : "&") + "as_of=" + encodeURIComponent(state.saveToken);
}

function setStale(on: boolean): void {
  if (state.saveMovedOn === on) return;
  state.saveMovedOn = on;
  staleChanged.emit();
}

export function onStale(listener: () => void): void {
  staleChanged.on(listener);
}

var tokenMoved = createListeners();

export function onToken(listener: () => void): void {
  tokenMoved.on(listener);
}

export function holdToken(token: string): void {
  var moved = state.saveToken !== token;
  state.saveToken = token;
  setStale(false);
  if (moved) tokenMoved.emit();
}

export function dropToken(): void {
  state.saveToken = "";
  setStale(false);
}

function readReply<T extends ApiError>(path: string, r: Response, sent?: string): Promise<T> {
  return r.json().then(function (body: T & { stale?: boolean }) {
    if (r.status === 409 && body.stale && sent && sent === state.saveToken) setStale(true);
    if (!r.ok || body.error) throw statusError(r.status, body.error || r.status + " " + path, body);
    return body;
  });
}

function fillPathParam(path: string, subject?: string): string {
  return subject === undefined ? path : path.replace(/\{[^}]+\}/, encodeURIComponent(subject));
}

function requestInit(method: string, body?: object): RequestInit {
  var init: RequestInit = { method: method };
  if (body) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return init;
}

export function get<T extends ApiError>(path: ApiUrl, subject?: string): Promise<T> {
  var url = fillPathParam(path, subject);
  var sent = state.saveToken;
  return fetch(withSaveToken(withSelection(url))).then(function (r) {
    return readReply<T>(url, r, sent);
  });
}

export function send<T extends ApiError>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: ApiPath,
  body?: object,
  subject?: string,
  query?: string
): Promise<T> {
  var url = fillPathParam(path, subject);
  if (query) url += "?" + query;
  return fetch(withSelection(url), requestInit(method, body)).then(function (r) {
    return readReply<T>(url, r);
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

function statusError(status: number, message: string, body?: ApiError): StatusError {
  var error: StatusError = new Error(message);
  error.status = status;
  if (body !== undefined) error.body = body;
  return error;
}

export function isNotFound(reason: unknown): boolean {
  return (reason as StatusError | null | undefined)?.status === 404;
}

export type Pushed<T, C> = { conflict: false; body: T } | { conflict: true; body: C };

export function postWithConflict<T extends ApiError, C extends ApiError>(path: ApiPath, body: object, subject?: string): Promise<Pushed<T, C>> {
  var url = fillPathParam(path, subject);
  return fetch(withSelection(url), requestInit("POST", body)).then(function (r) {
    return r
      .json()
      .catch(function () {
        return {};
      })
      .then(function (payload: ApiError): Pushed<T, C> {
        if (r.status === 409) return { conflict: true, body: payload as C };
        if (!r.ok || payload.error) throw statusError(r.status, payload.error || r.status + " " + url);
        return { conflict: false, body: payload as T };
      });
  });
}
