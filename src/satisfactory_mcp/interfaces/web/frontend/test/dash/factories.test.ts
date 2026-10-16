import { describe, expect, it } from "vitest";

import { factoryAddress, factoryDash } from "../../src/dash/factories/address";
import { boostsOf, floorBelow, floorsOf, groupMachines, mixParts, pickFloor, sortFactories, worstState } from "../../src/dash/factories/model";
import { deckCells, frameAround, pageTurn, place, shapeDefs, turned } from "../../src/dash/factories/plan-geometry";

import type { FactoryHealthRow, FloorBand, FloorsResponse, PlacementRow, ShardsResponse, SloopsResponse } from "../../src/api/shapes";

function machine(over: Partial<PlacementRow>): PlacementRow {
  return {
    instance_leaf: "m",
    cls: "Build_ConstructorMk1_C",
    name: "Constructor",
    x_m: 0,
    y_m: 0,
    z_m: 0,
    recipe: "Recipe_IronRod_C",
    recipe_name: "Iron Rod",
    clock: 1,
    paused: false,
    state: "saturated",
    actionable: false,
    uptime: 1,
    yaw: 0,
    w_m: 4,
    l_m: 5,
    h_m: 6,
    factory: "f",
    ...over,
  };
}

function band(ordinal: number, machines: string[], over?: Partial<FloorBand>): FloorBand {
  return {
    ordinal,
    top_m: ordinal * 8,
    low_m: null,
    high_m: null,
    span_m: null,
    pieces: 1,
    cells: 1,
    area_m2: 64,
    share: 1,
    minor: false,
    machines,
    attachments: [],
    deck_rows: [],
    machine_count: machines.length,
    attachment_count: 0,
    deck_row_count: 0,
    ...over,
  };
}

describe("factory address", () => {
  it("reads name, aspect and floor, and writes them back", () => {
    expect(factoryAddress("Iron Works/machines?floor=2")).toEqual({ name: "Iron Works", aspect: "machines", floor: "2" });
    expect(factoryAddress("Iron Works?floor=all")).toEqual({ name: "Iron Works", aspect: "", floor: "all" });
    expect(factoryAddress("a/flows", (n) => n === "a/flows")).toEqual({ name: "a/flows", aspect: "", floor: "" });
    expect(factoryDash("Iron Works", "flows", "1")).toBe("factories/Iron Works/flows?floor=1");
  });
});

describe("factories list", () => {
  const row = (name: string, actionable: number, uptime: number | null, mw: number) => ({ name, actionable, uptime, measured_mw: mw }) as FactoryHealthRow;
  const rows = [row("b", 0, 0.9, 10), row("a", 3, 0.2, 5), row("c", 1, null, 30)];

  it("sorts by need action, name, uptime and draw", () => {
    expect(sortFactories(rows, "need").map((r) => r.name)).toEqual(["a", "c", "b"]);
    expect(sortFactories(rows, "name").map((r) => r.name)).toEqual(["a", "b", "c"]);
    expect(sortFactories(rows, "uptime").map((r) => r.name)).toEqual(["a", "b", "c"]);
    expect(sortFactories(rows, "draw").map((r) => r.name)).toEqual(["c", "b", "a"]);
  });

  it("orders the state mix fine first, worst last", () => {
    const tone = (s: string) => (s === "starved" ? "bad" : s === "blocked" ? "blocked" : "ok");
    expect(mixParts([{ state: "starved", count: 2 }, { state: "saturated", count: 5 }, { state: "blocked", count: 1 }, { state: "unmonitored", count: 1 }], tone)).toEqual([
      ["ok", 6],
      ["blocked", 1],
      ["bad", 2],
    ]);
  });
});

describe("machines and floors", () => {
  const rows = [
    machine({ instance_leaf: "a", state: "starved", clock: 1 }),
    machine({ instance_leaf: "b", clock: 0.5 }),
    machine({ instance_leaf: "c", name: "Smelter", cls: "Build_SmelterMk1_C", recipe_name: "Iron Ingot" }),
  ];

  it("groups by building and recipe with the mean clock", () => {
    const { groups, of } = groupMachines(rows);
    expect(groups.map((g) => [g.building, g.recipe, g.ids.length, g.clock])).toEqual([
      ["Constructor", "Iron Rod", 2, 0.75],
      ["Smelter", "Iron Ingot", 1, 1],
    ]);
    expect(of["c"]).toBe(1);
    expect(worstState(groups[0]!.states, ["starved", "saturated"])).toBe("starved");
  });

  it("keeps the floors holding this factory's machines, top down, decks over the lowest", () => {
    const body = { platforms: [{ index: 0, bands: [band(0, ["a", "x"]), band(1, ["b"]), band(2, ["y"])] }] } as unknown as FloorsResponse;
    const floors = floorsOf(body, { a: 1, b: 1, c: 1 });
    expect(floors.map((f) => [f.ordinal, f.ids, f.deck])).toEqual([
      [1, ["b"], 8],
      [0, ["a"], 0],
    ]);
    expect(floorBelow(floors, 1)).toBe(0);
    expect(floorBelow(floors, 0)).toBeNull();
  });

  it("opens on the floor with most machines needing action unless one is asked for", () => {
    const body = { platforms: [{ index: 0, bands: [band(0, ["a"]), band(1, ["b"], { area_m2: 500 })] }] } as unknown as FloorsResponse;
    const floors = floorsOf(body, { a: 1, b: 1 });
    expect(pickFloor(floors, "", (id) => id === "a")).toBe(0);
    expect(pickFloor(floors, "", () => false)).toBe(1);
    expect(pickFloor(floors, "all", () => false)).toBe(-1);
    expect(pickFloor(floors, "1", (id) => id === "a")).toBe(1);
    expect(pickFloor(floors.slice(0, 1), "", () => true)).toBe(-1);
  });

  it("adds shards and sloops to the group their holder runs in", () => {
    const { groups } = groupMachines(rows);
    const shards = { holders: [{ instance: "Persistent_Level:PersistentLevel.a", slotted: 2, idle: 1 }] } as unknown as ShardsResponse;
    const sloops = { holders: [{ instance: "c", sloops: 1, boost: 2, boost_in_save: 1.5 }] } as unknown as SloopsResponse;
    expect(boostsOf(groups, shards, sloops)).toEqual({
      0: { shards: 2, idle: 1, sloops: 0, disagrees: false },
      1: { shards: 0, idle: 0, sloops: 1, disagrees: true },
    });
  });
});

describe("plan geometry", () => {
  it("turns the page by the most common deck yaw, modulo 90", () => {
    expect(pageTurn([{ yaw: 100 }, { yaw: 10 }, { yaw: 45 }], [])).toBe(10);
    expect(pageTurn([], [{ yaw: -80 }])).toBe(10);
    expect(pageTurn([], [])).toBe(0);
    const p = turned([10, 0], [0, 0], 90);
    expect(p[0]).toBeCloseTo(0);
    expect(p[1]).toBeCloseTo(-10);
  });

  it("falls back to the measured box and frames every piece", () => {
    const pieces = [{ id: "a", cls: "X", x: 0, y: 0, yaw: 0, w: 2, l: 3 }];
    const { defs, of } = shapeDefs(pieces, {});
    expect(defs[0]!.outline).toBe("M-2 -3H2V3H-2Z");
    const frame = frameAround(place(pieces, of, [0, 0], 0), defs)!;
    expect(frame[0]).toBeLessThan(-3.6);
    expect(frame[2]).toBeGreaterThan(7.2);
  });

  it("snaps deck pieces to cells and draws only the outer edge", () => {
    const decks = [
      { floor: 0, x: 0, y: 0, yaw: 0 },
      { floor: 0, x: 8, y: 0, yaw: 0 },
    ];
    const cells = deckCells(decks, [0, 0], 0, [0, 0]);
    expect(cells.cells).toBe("M-4 -4h8v8h-8zM4 -4h8v8h-8z");
    expect(cells.edge.split("M").length - 1).toBe(6);
  });
});
