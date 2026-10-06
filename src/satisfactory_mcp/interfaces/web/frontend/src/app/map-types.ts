/* The map type registry as the page holds it: `/api/maps`, fetched at boot and again whenever
 * a `maps` event says the registry moved. The switcher (tiles.ts) and the Maps tab (settings-maps.ts)
 * both read it here, so neither imports the other. docs/maps_contract.md §6. */

import { get } from "../api/client";
import { createListeners } from "./listeners";
import { friendlyError } from "../kit/toast";

import type { MapJobBody, MapsResponse, MapTypeBody } from "../api/shapes";

/** The `maps` event: the job that moved (null for a registry change) and the list's version. */
export interface MapsEvent {
  job: MapJobBody | null;
  queued: string[];
  registry_version: number;
}

export var mapRegistry: { body: MapsResponse | null; failed: string } = { body: null, failed: "" };

/* Told on every change; `listed` is true when the TYPES changed, false for job progress. */
var registryListeners = createListeners<[boolean]>();

export function onMapRegistry(listener: (listed: boolean) => void): void {
  registryListeners.on(listener);
}

function notifyListeners(listed: boolean): void {
  registryListeners.emit(listed);
}

var inflight: Promise<MapsResponse | null> | null = null;

export function fetchMapRegistry(): Promise<MapsResponse | null> {
  if (inflight) return inflight;
  inflight = get<MapsResponse>("/api/maps")
    .then(function (body) {
      mapRegistry.body = body;
      mapRegistry.failed = "";
      return body;
    })
    .catch(function (reason) {
      mapRegistry.failed = friendlyError(reason);
      return null;
    })
    .then(function (body) {
      inflight = null;
      notifyListeners(true);
      return body;
    });
  return inflight;
}

/** A reply that is the whole list again, as every registry write answers. */
export function adoptMapRegistry(body: MapsResponse): void {
  mapRegistry.body = body;
  mapRegistry.failed = "";
  notifyListeners(true);
}

export function onMapsEvent(event: MapsEvent): void {
  var body = mapRegistry.body;
  if (!body || event.registry_version !== body.version) {
    fetchMapRegistry();
    return;
  }
  var job = event.job;
  if (job) {
    var at = body.jobs.findIndex(function (row) {
      return row.id === job!.id;
    });
    if (at >= 0) body.jobs[at] = job;
    else body.jobs.unshift(job);
  }
  notifyListeners(false);
}

/** What a type technically is: style, renderer, data build and heightfield, then its size,
 *  which the name already ends in when another type has the same axes. Its title is
 *  `row.title`, composed by the server so the page and chat agree. */
export function mapTypeAxes(row: MapTypeBody): string {
  var size = row.size_px ? " · " + row.size_px + " px" : "";
  var named = size && row.name.slice(-size.length) === size;
  return row.name + (named ? "" : size);
}

/** The one amber word a stale type carries, or "" for a type whose data is current. */
export function staleLabel(row: MapTypeBody): string {
  if (!row.freshness.stale.length) return "";
  return row.freshness.stale.some(function (stale) {
    return stale.axis === "game";
  })
    ? "older build"
    : "older data";
}

export function staleReasons(row: MapTypeBody): string {
  return row.freshness.stale
    .map(function (stale) {
      return stale.text;
    })
    .join("; ");
}
