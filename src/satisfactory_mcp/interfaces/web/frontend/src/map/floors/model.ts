/* Reading `/api/floors`: which band a thing is on, what one floor owns, and the one height
 * rule, `standsOn`, for the layers the payload does not cover. Nothing here touches the map. */

import type { FloorBand, FloorDeck, FloorPlatform, FloorRun, FloorsResponse } from "../../api/shapes";
import type { Point3M } from "../geometry";
import type { FloorMark } from "../leaflet-private";

/** The pseudo-floor's key, in the picker and in the fragment alike. */
export var GROUND = "ground";

/* How deep the concrete under a deck is, metres: half the thickest foundation the game builds
 * (`8x4`), so a thing lower than this is under the floor rather than on it. The one number in
 * floor mode that is not the server's, used only for the layers no band lists. */
export var DECK_DEPTH_M = 2;

export function bandOf(platform: FloorPlatform | null, key: string): FloorBand | null {
  if (!platform || key === GROUND) return null;
  let found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (String(band.ordinal) === key) found = band;
  });
  return found;
}

/* A band's ceiling: the next deck up, or nothing at all above the top floor. Open at the top
 * on purpose, so a roof on the highest deck still lands on exactly one floor. */
function ceilingOf(platform: FloorPlatform, band: FloorBand): number {
  let top = Number.POSITIVE_INFINITY;
  const here = band.top_m === null ? 0 : band.top_m;
  platform.bands.forEach(function (other) {
    const level = other.top_m;
    if (level !== null && level > here && level < top) top = level;
  });
  return top;
}

/** Does a thing at `z_m` stand on this band: from this deck's top surface up to the next
 *  deck's, each less the concrete it is poured into. */
export function standsOn(platform: FloorPlatform, band: FloorBand, z_m: number): boolean {
  const top = band.top_m;
  if (top === null) return false;
  return z_m >= top - DECK_DEPTH_M && z_m < ceilingOf(platform, band) - DECK_DEPTH_M;
}

/** Every instance id this floor lists, as a lookup. */
export function instanceIdsOn(band: FloorBand | null, body: FloorsResponse): Record<string, boolean> {
  const ids: Record<string, boolean> = {};
  if (band) {
    band.machines.concat(band.attachments).forEach(function (id) {
      ids[id] = true;
    });
    return ids;
  }
  // The ground: the API's three ways of not being on a floor. `exempt` is a miner on a node or
  // a pump on water; `off-deck` is what `terrain` degrades to where there is no heightfield.
  ["exempt", "terrain", "off-deck"].forEach(function (group) {
    (body.placements[group] || []).forEach(function (row) {
      ids[row.instance_leaf] = true;
    });
  });
  return ids;
}

/** Which foundation rows this floor's deck is made of. The ground gets none: drawing the
 *  storey above it underneath it would be an invention. */
export function deckRows(band: FloorBand | null): Record<number, boolean> {
  const rows: Record<number, boolean> = {};
  if (band) {
    band.deck_rows.forEach(function (row) {
      rows[row] = true;
    });
  }
  return rows;
}

/** The key a run is filed under, the same for a payload run and a drawn piece's mark. */
export function runKey(run: { kind: string; key: number }): string {
  return run.kind + ":" + run.key;
}

/** Is this end of a run on this band of this platform? */
export function isEndOnBand(end: FloorDeck | null, platform: number, band: FloorBand): boolean {
  return !!end && end.platform === platform && end.ordinal === band.ordinal;
}

/* Which runs belong on this floor: same-deck runs on this band, plus every CONNECTOR with an end
 * on it, since a connector is drawn on every floor it touches. The ground takes the runs that
 * never reach a deck (`terrain`) and those with one end on one (`mixed`). The runs themselves
 * come back, because a connector's glyph has to say where the other end goes. */
export function runsOn(
  body: FloorsResponse,
  band: FloorBand | null,
  platform: number
): Record<string, FloorRun> {
  const runs: Record<string, FloorRun> = {};
  function put(run: FloorRun): void {
    runs[runKey(run)] = run;
  }
  if (!band) {
    ["terrain", "mixed"].forEach(function (membership) {
      (body.runs[membership] || []).forEach(put);
    });
    return runs;
  }
  (body.runs["same-deck"] || []).forEach(function (run) {
    if (isEndOnBand(run.ends[0] || null, platform, band)) put(run);
  });
  (body.runs["connector"] || []).forEach(function (run) {
    const touches = run.ends.some(function (end) {
      return isEndOnBand(end || null, platform, band);
    });
    if (touches) put(run);
  });
  return runs;
}

/** Which band of this platform lists an instance id, if any. */
export function bandOfId(platform: FloorPlatform, id: string): FloorBand | null {
  let found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (band.machines.indexOf(id) >= 0) found = band;
  });
  return found;
}

/* Does a machine standing on a lower deck come up through this floor? A null height is a
 * height never recorded, not a short machine, so it draws no ghost rather than a guessed one. */
export function piercesFloor(platform: FloorPlatform, band: FloorBand, mark: FloorMark): boolean {
  if (mark.z_m === undefined || mark.h_m === undefined || mark.h_m === null) return false;
  if (mark.id === undefined) return false;
  const deck = band.top_m;
  if (deck === null) return false;
  const stands = bandOfId(platform, mark.id);
  if (!stands || stands.top_m === null || stands.top_m >= deck) return false;
  return mark.z_m + mark.h_m > deck;
}

/* Which band a point stands on BY HEIGHT ALONE: weaker than the storage rule, because deck
 * cells are only collected for the band being looked at, and this has to ask about the others. */
export function bandAtHeight(platform: FloorPlatform, z_m: number): FloorBand | null {
  let found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (standsOn(platform, band, z_m)) found = band;
  });
  return found;
}

/* Is this point on the floor being looked at: over this deck's own cells AND between this deck
 * and the next. `cells` is passed in because the deck pieces of the same pass fill it. */
export function onThisFloor(
  platform: FloorPlatform,
  band: FloorBand,
  cells: Record<string, boolean>,
  cellKey: (x_m: number, y_m: number) => string,
  at: Point3M
): boolean {
  return cells[cellKey(at[0], at[1])] === true && standsOn(platform, band, at[2]);
}

/** The end of one drawn piece nearest this floor's deck, in game metres. */
export function endNearest(mark: FloorMark, deck: number): Point3M | null {
  if (!mark.ends) return null;
  const head = mark.ends[0];
  const tail = mark.ends[1];
  return Math.abs(head[2] - deck) <= Math.abs(tail[2] - deck) ? head : tail;
}

/* Which floor to open on: the busiest storey, not floor zero, because a tower's lowest deck is
 * often the bare slab it stands on. */
export function busiestBand(platform: FloorPlatform): FloorBand | null {
  let best: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (best === null || band.machine_count > best.machine_count) best = band;
  });
  return best;
}

export function groundPlacements(body: FloorsResponse): number {
  const counts = body.counts.placements;
  return (counts["exempt"] || 0) + (counts["terrain"] || 0) + (counts["off-deck"] || 0);
}

export function groundRuns(body: FloorsResponse): number {
  const counts = body.counts.membership;
  return (counts["terrain"] || 0) + (counts["mixed"] || 0);
}

export function hasGround(body: FloorsResponse): boolean {
  return groundPlacements(body) + groundRuns(body) > 0;
}
