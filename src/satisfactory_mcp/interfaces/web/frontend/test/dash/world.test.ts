import { describe, expect, it } from "vitest";

import { fromLegacy, legacyWorldDash, worldAddress, worldDash, worldScrollKey } from "../../src/dash/world/address";
import { fieldOutline, groupNodes, hull, pickups, sortNodes } from "../../src/dash/world/model";

import type { CollectibleRow, FoundField, FoundNode } from "../../src/api/shapes";

function node(id: string, status: FoundNode["status"], distance: number | null, rate: number, region?: string): FoundNode {
  return {
    id,
    name: id,
    resource: "Desc_OreIron_C",
    resource_name: "Iron Ore",
    purity: "normal",
    kind: "node",
    x_m: 0,
    y_m: 0,
    z_m: 0,
    grid: "X1Y1",
    rate,
    status,
    occupant: null,
    occupant_off: null,
    region: region ? { name: region, confidence: "", accuracy_m: 0, certain: true, text: region } : null,
    distance_m: distance,
    moved: false,
    spoiler: false,
  };
}

describe("the World address", () => {
  it("reads the defaults and drops them when written", () => {
    const a = worldAddress("");
    expect(a).toMatchObject({ show: "nodes", near: "me", group: "none", sort: "nearest", sel: "" });
    expect(worldDash(a)).toBe("world");
    expect(worldDash({ show: "pickups", mode: "collected" })).toBe("world?show=pickups&mode=collected");
  });

  it("keeps a group and sort only where they belong", () => {
    expect(worldAddress("?show=conduits&group=field").group).toBe("run");
    expect(worldAddress("?group=field&sort=rank").sort).toBe("nearest");
    expect(worldAddress("?group=field&sort=rank&res=Desc_OreIron_C").sort).toBe("rank");
    expect(worldAddress("?show=pickups&res=Desc_OreIron_C").res).toBe("");
  });

  it("round-trips a pick and a place", () => {
    const dash = worldDash({ near: "Iron Works", sel: "n-1", res: "Desc_OreIron_C" });
    expect(worldAddress(dash.slice(5))).toMatchObject({ near: "Iron Works", sel: "n-1", res: "Desc_OreIron_C" });
  });

  it("keys the scroll by the query, not the pick", () => {
    expect(worldScrollKey("?show=pickups&sel=p-1")).toBe(worldScrollKey("?show=pickups"));
    expect(worldScrollKey("?show=pickups")).not.toBe(worldScrollKey("?show=conduits"));
  });

  it("lands every old view", () => {
    expect(legacyWorldDash("world", "")).toBeNull();
    expect(legacyWorldDash("world", "?show=pickups")).toBeNull();
    expect(legacyWorldDash("world", "nodes?near=me&status=free&resource=Desc_OreIron_C")).toBe("world?res=Desc_OreIron_C&status=free");
    expect(legacyWorldDash("world", "fields?near=12,34")).toBe("world?near=12%2C34&group=field");
    expect(legacyWorldDash("world", "rank?resource=Desc_Coal_C&at=hub&within_m=2000")).toBe("world?res=Desc_Coal_C&near=hub&group=field&sort=rank&within=2000");
    expect(legacyWorldDash("world", "regions")).toBe("world?group=region");
    expect(legacyWorldDash("world", "conduits?near=chain:7&view=networks")).toBe("world?show=conduits&near=chain%3A7&group=network");
    expect(legacyWorldDash("world", "pickups?view=collected&group=somersloop")).toBe("world?show=pickups&mode=collected&kind=somersloop");
    expect(fromLegacy("bogus", {})).toBe("world");
  });
});

describe("the World rows", () => {
  it("sorts nearest first, unknown distance last, or by rate", () => {
    const rows = [node("a", "free", null, 60), node("b", "free", 50, 30), node("c", "tapped", 10, 120)];
    expect(sortNodes(rows, "nearest").map((n) => n.id)).toEqual(["c", "b", "a"]);
    expect(sortNodes(rows, "rate").map((n) => n.id)).toEqual(["c", "a", "b"]);
  });

  it("groups by status in a fixed order, or by region fullest first", () => {
    const rows = [node("a", "tapped", 1, 1, "Swamp"), node("b", "free", 1, 1, "Dune"), node("c", "free", 1, 1, "Dune"), node("d", "locked", 1, 1)];
    expect(groupNodes(rows, "none").map((g) => g.label)).toEqual(["free", "tapped", "locked"]);
    expect(groupNodes(rows, "region").map((g) => [g.label, g.rows.length])).toEqual([
      ["Dune", 2],
      ["off the map", 1],
      ["Swamp", 1],
    ]);
  });

  it("outlines a field by its nodes' hull, or its box when none is placed", () => {
    expect(
      hull([
        [0, 0],
        [2, 0],
        [1, 1],
        [2, 2],
        [0, 2],
      ])
    ).toEqual([
      [0, 0],
      [2, 0],
      [2, 2],
      [0, 2],
    ]);
    const field = { members: ["x"], bbox_m: [0, 0, 4, 2] } as unknown as FoundField;
    expect(fieldOutline(field, () => null)).toEqual([
      [0, 0],
      [4, 0],
      [4, 2],
      [0, 2],
    ]);
    expect(fieldOutline(field, () => [5, 5])).toEqual([[5, 5]]);
  });

  it("measures a pickup's distance only where the server sent none", () => {
    const sent = { category: "somersloop", name: "a", x_m: 0, y_m: 0, distance_m: 7 } as CollectibleRow;
    const bare = { category: "mercer_sphere", name: "b", x_m: 3, y_m: 4, distance_m: null } as CollectibleRow;
    const out = pickups([sent, bare], [0, 0], "nearest", (c) => c);
    expect(out.map((p) => [p.row.name, p.distance_m, p.measured])).toEqual([
      ["b", 5, true],
      ["a", 7, false],
    ]);
    expect(pickups([sent, bare], [0, 0], "kind", (c) => c).map((p) => p.row.name)).toEqual(["b", "a"]);
  });
});
