import { describe, expect, it } from "vitest";

import {
  bandAtHeight,
  bandOf,
  bandOfId,
  busiestBand,
  deckRows,
  endNearest,
  GROUND,
  groundPlacements,
  groundRuns,
  hasGround,
  instanceIdsOn,
  isEndOnBand,
  onThisFloor,
  piercesFloor,
  runKey,
  runsOn,
  standsOn,
} from "../../../src/map/floors/model";

import type { FloorBand, FloorDeck, FloorPlatform, FloorRun, FloorsResponse } from "../../../src/api/shapes";

function band(ordinal: number, top_m: number | null, extra: Partial<FloorBand> = {}): FloorBand {
  return {
    ordinal: ordinal,
    top_m: top_m,
    low_m: null,
    high_m: null,
    span_m: null,
    pieces: 0,
    cells: 0,
    area_m2: 0,
    share: 0,
    minor: false,
    machines: [],
    attachments: [],
    deck_rows: [],
    machine_count: 0,
    attachment_count: 0,
    deck_row_count: 0,
    ...extra,
  };
}

function platform(bands: FloorBand[]): FloorPlatform {
  return { index: 0, cells: 0, pieces: 0, area_m2: 0, centre_m: [], extent_m: [], clean: 0, label: null, slab: null, bands: bands };
}

function deck(ordinal: number): FloorDeck {
  return { platform: 0, ordinal: ordinal, top_m: null };
}

function run(kind: string, key: number, ends: (FloorDeck | null)[]): FloorRun {
  return { kind: kind, key: key, pieces: 1, lift: false, rise_m: null, riser: false, ends: ends };
}

function body(parts: Partial<FloorsResponse>): FloorsResponse {
  return {
    note: null,
    selection: null,
    terrain_measured: true,
    counts: { platforms: 0, bands: 0, runs: 0, violations: 0, placements: {}, membership: {} },
    platforms: [],
    runs: {},
    placements: {},
    violations: [],
    rules: {} as FloorsResponse["rules"],
    ...parts,
  };
}

const placement = (leaf: string) => ({ instance_leaf: leaf, cls: "", name: "", kind: "", x_m: 0, y_m: 0, z_m: 0, above_terrain_m: 0 });

/* Three decks eight metres apart; the middle one is the busiest. */
const SLAB = band(0, 0, { machines: ["m0"], machine_count: 1 });
const FIRST = band(1, 8, { machines: ["m1", "m2"], attachments: ["a1"], deck_rows: [3, 5], machine_count: 2 });
const ROOF = band(2, 16, { machine_count: 2 });
const TOWER = platform([SLAB, FIRST, ROOF]);

describe("which band", () => {
  it("finds a band by its ordinal, never for the ground", () => {
    expect(bandOf(TOWER, "1")).toBe(FIRST);
    expect(bandOf(TOWER, "7")).toBeNull();
    expect(bandOf(TOWER, GROUND)).toBeNull();
    expect(bandOf(null, "1")).toBeNull();
  });

  it("stands a thing on a deck from its concrete up to the next deck's", () => {
    expect(standsOn(TOWER, SLAB, -2)).toBe(true);
    expect(standsOn(TOWER, SLAB, -2.1)).toBe(false);
    expect(standsOn(TOWER, SLAB, 5.9)).toBe(true);
    expect(standsOn(TOWER, SLAB, 6)).toBe(false);
    expect(standsOn(TOWER, ROOF, 1000)).toBe(true);
    expect(standsOn(TOWER, band(3, null), 0)).toBe(false);
    expect(bandAtHeight(TOWER, 10)).toBe(FIRST);
    expect(bandAtHeight(TOWER, -50)).toBeNull();
  });

  it("finds the band that lists an instance, and the busiest one", () => {
    expect(bandOfId(TOWER, "m2")).toBe(FIRST);
    expect(bandOfId(TOWER, "nowhere")).toBeNull();
    expect(busiestBand(TOWER)).toBe(FIRST);
    expect(busiestBand(platform([]))).toBeNull();
  });
});

describe("what a floor owns", () => {
  it("lists a band's machines and attachments, or the ground's three groups", () => {
    expect(instanceIdsOn(FIRST, body({}))).toEqual({ m1: true, m2: true, a1: true });
    const ground = body({
      placements: { exempt: [placement("miner")], terrain: [placement("pump")], "off-deck": [placement("box")], "on-deck": [placement("no")] },
    });
    expect(instanceIdsOn(null, ground)).toEqual({ miner: true, pump: true, box: true });
  });

  it("takes deck rows from the band only", () => {
    expect(deckRows(FIRST)).toEqual({ 3: true, 5: true });
    expect(deckRows(null)).toEqual({});
  });

  it("files runs by kind and key, connectors on every floor they touch", () => {
    const flat = run("belt", 1, [deck(1), deck(1)]);
    const elsewhere = run("belt", 2, [deck(2), deck(2)]);
    const riser = run("pipe", 3, [deck(0), deck(1)]);
    const loose = run("belt", 4, [null, null]);
    const mixed = run("belt", 5, [deck(0), null]);
    const floors = body({ runs: { "same-deck": [flat, elsewhere], connector: [riser], terrain: [loose], mixed: [mixed] } });
    expect(runKey(riser)).toBe("pipe:3");
    expect(isEndOnBand(deck(1), 0, FIRST)).toBe(true);
    expect(isEndOnBand(null, 0, FIRST)).toBe(false);
    expect(isEndOnBand(deck(1), 1, FIRST)).toBe(false);
    expect(Object.keys(runsOn(floors, FIRST, 0)).sort()).toEqual(["belt:1", "pipe:3"]);
    expect(Object.keys(runsOn(floors, SLAB, 0))).toEqual(["pipe:3"]);
    expect(Object.keys(runsOn(floors, null, 0)).sort()).toEqual(["belt:4", "belt:5"]);
  });

  it("counts what stands on the ground", () => {
    const counted = body({
      counts: { platforms: 1, bands: 3, runs: 0, violations: 0, placements: { exempt: 2, "off-deck": 1 }, membership: { mixed: 4 } },
    });
    expect(groundPlacements(counted)).toBe(3);
    expect(groundRuns(counted)).toBe(4);
    expect(hasGround(counted)).toBe(true);
    expect(hasGround(body({}))).toBe(false);
  });
});

describe("height rules", () => {
  it("ghosts a machine from a lower deck that reaches through this one", () => {
    expect(piercesFloor(TOWER, FIRST, { id: "m0", z_m: 0, h_m: 12 })).toBe(true);
    expect(piercesFloor(TOWER, FIRST, { id: "m0", z_m: 0, h_m: 5 })).toBe(false);
    expect(piercesFloor(TOWER, FIRST, { id: "m0", z_m: 0, h_m: null })).toBe(false);
    expect(piercesFloor(TOWER, FIRST, { z_m: 0, h_m: 12 })).toBe(false);
    expect(piercesFloor(TOWER, FIRST, { id: "m1", z_m: 8, h_m: 12 })).toBe(false);
    expect(piercesFloor(TOWER, FIRST, { id: "stray", z_m: 0, h_m: 12 })).toBe(false);
    expect(piercesFloor(TOWER, band(9, null), { id: "m0", z_m: 0, h_m: 12 })).toBe(false);
  });

  it("puts a point on this floor only over its own cells and between its decks", () => {
    const cells = { "1,2": true };
    const key = (x: number, y: number) => x + "," + y;
    expect(onThisFloor(TOWER, FIRST, cells, key, [1, 2, 9])).toBe(true);
    expect(onThisFloor(TOWER, FIRST, cells, key, [1, 2, 20])).toBe(false);
    expect(onThisFloor(TOWER, FIRST, cells, key, [5, 5, 9])).toBe(false);
  });

  it("picks the end of a piece nearest the deck", () => {
    const ends: [[number, number, number], [number, number, number]] = [
      [0, 0, 0],
      [0, 0, 16],
    ];
    expect(endNearest({ ends: ends }, 14)).toBe(ends[1]);
    expect(endNearest({ ends: ends }, 8)).toBe(ends[0]);
    expect(endNearest({}, 8)).toBeNull();
  });
});
