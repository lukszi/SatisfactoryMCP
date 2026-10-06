/* The map type registry as the page holds it: `/api/maps`, fetched at boot and again whenever
 * a `maps` event says the registry moved. The switcher (tiles.ts) and the Maps tab (settings-maps.ts)
 * both read it here, so neither imports the other. docs/maps_contract.md §6. */

import { get } from "../api/client";
import { friendly } from "../kit/toast";

import type { MapJobBody, MapsResponse, MapTypeBody } from "../api/shapes";

/** The `maps` event: the job that moved (null for a registry change) and the list's version. */
export interface MapsEvent {
  job: MapJobBody | null;
  queued: string[];
  registry_version: number;
}

export var mapState: { body: MapsResponse | null; failed: string } = { body: null, failed: "" };

/* Told on every change; `listed` is true when the TYPES changed, false for job progress. */
var listeners: Array<(listed: boolean) => void> = [];

export function onMaps(listener: (listed: boolean) => void): void {
  listeners.push(listener);
}

function tell(listed: boolean): void {
  listeners.forEach(function (listener) {
    listener(listed);
  });
}

var inflight: Promise<MapsResponse | null> | null = null;

export function fetchMaps(): Promise<MapsResponse | null> {
  if (inflight) return inflight;
  inflight = get<MapsResponse>("/api/maps")
    .then(function (body) {
      mapState.body = body;
      mapState.failed = "";
      return body;
    })
    .catch(function (reason) {
      mapState.failed = friendly(reason);
      return null;
    })
    .then(function (body) {
      inflight = null;
      tell(true);
      return body;
    });
  return inflight;
}

/** A reply that is the whole list again, as every registry write answers. */
export function adoptMaps(body: MapsResponse): void {
  mapState.body = body;
  mapState.failed = "";
  tell(true);
}

export function onMapsEvent(event: MapsEvent): void {
  var body = mapState.body;
  if (!body || event.registry_version !== body.version) {
    fetchMaps();
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
  tell(false);
}

export function mapType(id: string): MapTypeBody | null {
  var body = mapState.body;
  if (!body) return null;
  return (
    body.types.filter(function (row) {
      return row.id === id;
    })[0] || null
  );
}

/** What a type technically is: style, renderer, data build and heightfield, then its size,
 *  which the name already ends in when another type has the same axes. Its title is
 *  `row.title`, composed by the server so the page and chat agree. */
export function mapDetails(row: MapTypeBody): string {
  var size = row.size_px ? " · " + row.size_px + " px" : "";
  var named = size && row.name.slice(-size.length) === size;
  return row.name + (named ? "" : size);
}

/** The one amber word a stale type carries, or "" for a type whose data is current. */
export function staleWord(row: MapTypeBody): string {
  if (!row.freshness.stale.length) return "";
  return row.freshness.stale.some(function (s) {
    return s.axis === "game";
  })
    ? "older build"
    : "older data";
}

export function staleWhy(row: MapTypeBody): string {
  return row.freshness.stale
    .map(function (s) {
      return s.text;
    })
    .join("; ");
}
