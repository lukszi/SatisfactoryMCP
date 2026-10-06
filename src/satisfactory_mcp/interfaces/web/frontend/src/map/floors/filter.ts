/* The floor filter: one pass over every filtered layer that keeps what stands on the floor being
 * looked at, ghosts what comes up through it, and adds an arrow to what leaves it. Idempotent,
 * and re-run after every redraw, because a refetch replaces a layer's contents. */

import { L } from "../leaflet";
import { boundsOfBbox, latLngOf } from "../map";
// Reached for a rule rather than a picture: `sinkRoutes` owns the stacking inside the three
// route layers, and rebuilding a group is exactly what disturbs it.
import { sinkRoutes } from "../drawn/routes";
import { state } from "../../app/state";
import { connectorGlyph, ghost, ghostRows, unghost, wireGlyph } from "./glyphs";
import {
  bandAtHeight,
  bandOf,
  deckRows,
  endNearest,
  instanceIdsOn,
  onThisFloor,
  piercesFloor,
  runKey,
  runsOn,
  standsOn,
} from "./model";

import type { FloorAddress } from "../../app/state";
import type { FloorBand, FloorPlatform, FloorRun, FloorsResponse } from "../../api/shapes";
import type { BboxM, Point3M } from "../geometry";
import type { Row } from "../../kit/dom";
import type { FloorMark } from "../leaflet-private";

/* Every layer floor mode filters. `foundations` FIRST: the storage and pole rules need this
 * deck's own 8 m cells, which the deck pieces of the same pass record. */
var FILTERED = [
  "foundations",
  "machines",
  "extractors",
  "generators",
  "belts",
  "pipes",
  "storage",
  "power",
];

/* Breathing room around a platform, metres: the same pad a factory-label flight uses, so a deck
 * that exactly fills the screen keeps the surroundings that say where it is. */
var FLOOR_PAD_M = 40;

/* Programmatic layer ticks are not the reader's decisions, and Leaflet fires `overlayadd` for
 * `map.addLayer` exactly as for a click; same flag, same reason, as regions.ts. */
var applying = false;

/** Run `action` with the layer events it causes marked as this mode's, not the reader's. */
export function withFilterGuard<T>(action: () => T): T {
  applying = true;
  try {
    return action();
  } finally {
    applying = false;
  }
}

/** Whether a layer event happening now was caused by floor mode itself. */
export function filterApplying(): boolean {
  return applying;
}

/** What a pass needs from the floor view: the answer, and the platform taken from it. */
export interface FilterSource {
  platform: FloorPlatform | null;
  body: FloorsResponse;
}

/** Everything one pass knows, worked out once before the layers are walked. */
interface FilterPass {
  platform: FloorPlatform;
  /** The band being looked at, or null for the ground pseudo-floor. */
  band: FloorBand | null;
  instanceIds: Record<string, boolean>;
  deckRows: Record<number, boolean>;
  runs: Record<string, FloorRun>;
  /** This deck's own grid cells, recorded by its foundation pieces as they are kept. */
  deckCells: Record<string, boolean>;
  /** Runs that already have their arrow: a chain is several pieces and gets one. */
  glyphedRuns: Record<string, boolean>;
  /** On the ground, the platform's padded extent, the scope its power rule needs. */
  groundReach: L.LatLngBounds | null;
  cellKey(x_m: number, y_m: number): string;
}

/** What one piece does on this floor: stay, become a ghost, and possibly bring an arrow. */
interface Verdict {
  keep: boolean;
  ghostRows?: Row[];
  glyph?: L.Marker;
}

var DROP: Verdict = { keep: false };
var KEEP: Verdict = { keep: true };

/** Everything a group holds, snapshotted once per redraw so that leaving can put it back. */
function snapshot(group: L.LayerGroup): L.Layer[] {
  if (!group._floorAll) {
    const all: L.Layer[] = [];
    group.eachLayer(function (piece) {
      all.push(piece);
    });
    group._floorAll = all;
  }
  return group._floorAll;
}

/* A platform's extent from the decks this page has drawn, padded in metres. Not from
 * `centre_m`: that is the pieces' mean, not the box's middle, and a box built round it clips
 * the deck at one edge. */
export function platformBounds(platform: FloorPlatform): L.LatLngBounds | null {
  const group = state.layers["foundations"];
  if (!group) return null;
  const rows: Record<number, boolean> = {};
  platform.bands.forEach(function (band) {
    band.deck_rows.forEach(function (row) {
      rows[row] = true;
    });
  });
  const xs: number[] = [];
  const ys: number[] = [];
  snapshot(group).forEach(function (piece) {
    const mark = piece._floor;
    if (!mark || mark.row === undefined || !rows[mark.row]) return;
    if (mark.x_m === undefined || mark.y_m === undefined) return;
    xs.push(mark.x_m);
    ys.push(mark.y_m);
  });
  if (!xs.length) return null;
  const extent: BboxM = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  return boundsOfBbox(extent, FLOOR_PAD_M);
}

function startFilterPass(view: { platform: FloorPlatform; body: FloorsResponse }, floor: FloorAddress): FilterPass {
  const platform = view.platform;
  const band = bandOf(platform, floor.band);
  const tile = view.body.rules.tile_m || 8;
  return {
    platform: platform,
    band: band,
    instanceIds: instanceIdsOn(band, view.body),
    deckRows: deckRows(band),
    runs: runsOn(view.body, band, platform.index),
    deckCells: {},
    glyphedRuns: {},
    // Measured once: it walks the whole foundations snapshot, and only the ground's power rule
    // asks for it.
    groundReach: band ? null : platformBounds(platform),
    cellKey: function (x_m, y_m) {
      return Math.floor(x_m / tile) + "," + Math.floor(y_m / tile);
    },
  };
}

/** Over this deck's own concrete AND between this deck and the next: storage and poles. */
function standsOnDeck(pass: FilterPass, band: FloorBand, mark: FloorMark): boolean {
  if (mark.x_m === undefined || mark.y_m === undefined || mark.z_m === undefined) return false;
  return !!pass.deckCells[pass.cellKey(mark.x_m, mark.y_m)] && standsOn(pass.platform, band, mark.z_m);
}

function classifyDeckPiece(pass: FilterPass, mark: FloorMark, row: number): Verdict {
  if (!pass.deckRows[row]) return DROP;
  if (mark.x_m !== undefined && mark.y_m !== undefined) {
    pass.deckCells[pass.cellKey(mark.x_m, mark.y_m)] = true;
  }
  return KEEP;
}

function classifyRunPiece(pass: FilterPass, mark: FloorMark, drawnRun: { kind: string; key: number }): Verdict {
  const key = runKey(drawnRun);
  const run = pass.runs[key];
  if (!run) return DROP;
  const band = pass.band;
  if (!band || band.top_m === null || pass.glyphedRuns[key]) return KEEP;
  if (!run.riser && !run.lift) return KEEP;
  const at = endNearest(mark, band.top_m);
  if (!at) return KEEP;
  pass.glyphedRuns[key] = true;
  return { keep: true, glyph: connectorGlyph(run, pass.platform.index, band, at) };
}

/* On the GROUND, a power piece is kept when it is near this factory and TOUCHES the ground: at
 * least one end in the platform's padded extent, and not both at a band's height. Every other
 * layer's ground row is the API's answer about this factory; power needs the scope invented. */
function classifyGroundPower(pass: FilterPass, mark: FloorMark): Verdict {
  const reach = pass.groundReach;
  if (!reach) return DROP;
  const anchor = mark.anchors || mark.ends;
  if (mark.ends && anchor) {
    if (!reach.contains(latLngOf(anchor[0])) && !reach.contains(latLngOf(anchor[1]))) return DROP;
    if (bandAtHeight(pass.platform, anchor[0][2]) && bandAtHeight(pass.platform, anchor[1][2])) return DROP;
    return KEEP;
  }
  if (mark.x_m === undefined || mark.y_m === undefined || mark.z_m === undefined) return DROP;
  if (!reach.contains(latLngOf([mark.x_m, mark.y_m]))) return DROP;
  return bandAtHeight(pass.platform, mark.z_m) ? DROP : KEEP;
}

/** A pole is placed exactly as a storage box is: no band lists it. */
function classifyPole(pass: FilterPass, band: FloorBand, mark: FloorMark): Verdict {
  return standsOnDeck(pass, band, mark) ? KEEP : DROP;
}

/* A wire is judged at its ANCHORS (the pole bases it ends at, where named) so a wire and its pole
 * cannot land on different storeys. Kept when either end is on this floor; the arrow needs the
 * narrower test, ends on different bands, since a cable leaving the platform level has left the
 * FACTORY, not the storey. Only `wire` earns an arrow; its `casing` would stack a second. */
function classifyWire(pass: FilterPass, band: FloorBand, mark: FloorMark, span: [Point3M, Point3M]): Verdict {
  const anchor = mark.anchors || span;
  const headHere = onThisFloor(pass.platform, band, pass.deckCells, pass.cellKey, anchor[0]);
  const tailHere = onThisFloor(pass.platform, band, pass.deckCells, pass.cellKey, anchor[1]);
  if (!headHere && !tailHere) return DROP;
  if (mark.power !== "wire") return KEEP;
  const from = headHere ? span[0] : span[1];
  const to = headHere ? span[1] : span[0];
  const fromAnchor = headHere ? anchor[0] : anchor[1];
  const toAnchor = headHere ? anchor[1] : anchor[0];
  const landsOn = bandAtHeight(pass.platform, toAnchor[2]);
  if (landsOn && landsOn.ordinal === band.ordinal) return KEEP;
  return { keep: true, glyph: wireGlyph(pass.platform, from, to, fromAnchor, toAnchor) };
}

/* The power grid is placed here because `/api/floors` never groups a wire: a pole is a point,
 * a wire has two ends, and the ground flips the question. */
function classifyPowerPiece(pass: FilterPass, mark: FloorMark): Verdict {
  const band = pass.band;
  if (!band) return classifyGroundPower(pass, mark);
  if (!mark.ends) return classifyPole(pass, band, mark);
  return classifyWire(pass, band, mark, mark.ends);
}

function classifyMachine(pass: FilterPass, piece: L.Layer, mark: FloorMark, id: string): Verdict {
  if (pass.instanceIds[id]) return KEEP;
  const band = pass.band;
  if (band && piece instanceof L.Path && piercesFloor(pass.platform, band, mark)) {
    return { keep: true, ghostRows: ghostRows(pass.platform, band, mark) };
  }
  return DROP;
}

/** Storage: joined by where it stands, because the decomposition never claimed it. */
function classifyStorage(pass: FilterPass, mark: FloorMark): Verdict {
  return pass.band && standsOnDeck(pass, pass.band, mark) ? KEEP : DROP;
}

function classifyPiece(pass: FilterPass, piece: L.Layer): Verdict {
  const mark = piece._floor;
  if (!mark) return DROP; // a piece nothing marked is a piece nothing can place
  if (mark.row !== undefined) return classifyDeckPiece(pass, mark, mark.row);
  if (mark.run) return classifyRunPiece(pass, mark, mark.run);
  if (mark.power) return classifyPowerPiece(pass, mark);
  if (mark.id !== undefined) return classifyMachine(pass, piece, mark, mark.id);
  return classifyStorage(pass, mark);
}

/** Refill a group with what this floor keeps, the arrows after it, and every ghost undone but
 *  this floor's. */
function rebuildGroup(group: L.LayerGroup, kept: L.Layer[], ghosted: L.Path[], glyphs: L.Layer[]): void {
  snapshot(group).forEach(function (piece) {
    if (piece instanceof L.Path && ghosted.indexOf(piece) < 0) unghost(piece);
  });
  group.clearLayers();
  kept.concat(glyphs).forEach(function (piece) {
    group.addLayer(piece);
  });
}

/* Rebuilding a group rebuilds its draw order, so the route stacking is put back afterwards: a
 * power casing left on top of its core would hide it. */
export function applyFilter(view: FilterSource | null, floor: FloorAddress | null): void {
  if (!view || !view.platform || !floor) return;
  const pass = startFilterPass({ platform: view.platform, body: view.body }, floor);
  withFilterGuard(function () {
    FILTERED.forEach(function (name) {
      const group = state.layers[name];
      if (!group) return;
      const kept: L.Layer[] = [];
      const ghosted: L.Path[] = [];
      const glyphs: L.Layer[] = [];
      snapshot(group).forEach(function (piece) {
        const verdict = classifyPiece(pass, piece);
        if (!verdict.keep) return;
        if (verdict.ghostRows) {
          ghost(piece as L.Path, verdict.ghostRows);
          ghosted.push(piece as L.Path);
        }
        kept.push(piece);
        if (verdict.glyph) glyphs.push(verdict.glyph);
      });
      rebuildGroup(group, kept, ghosted, glyphs);
    });
    sinkRoutes();
  });
}

/** Put every layer back the way its drawing module left it. */
export function clearFilter(): void {
  withFilterGuard(function () {
    FILTERED.forEach(function (name) {
      const group = state.layers[name];
      if (!group || !group._floorAll) return;
      const all = group._floorAll;
      delete group._floorAll;
      group.clearLayers();
      all.forEach(function (piece) {
        if (piece instanceof L.Path) unghost(piece);
        group.addLayer(piece);
      });
    });
    // `_floorAll` is in add order, so this puts each power casing back under its core.
    sinkRoutes();
  });
}
