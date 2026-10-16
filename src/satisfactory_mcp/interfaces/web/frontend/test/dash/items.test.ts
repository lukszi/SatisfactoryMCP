import { describe, expect, it } from "vitest";

import { itemsAddress, itemsDash, slug } from "../../src/dash/items/address";
import { boxFill, boxName, buildIndex, compact, driveOffers, findItem, groupBoxes, groupItems, heldGroup, placesOf, shownInCatalogue, shownRecipe } from "../../src/dash/items/model";

import type { HardDrivesResponse, ItemRow, StockPile, StockPlace } from "../../src/api/shapes";

function row(name: string, category: string, over?: Partial<ItemRow>): ItemRow {
  return { cls: "Desc_" + name.replace(/\W/g, "") + "_C", name, fluid: category === "fluids", energy_mj: 0, sink_points: 0, category, locked: false, ...over };
}

function pile(r: ItemRow, storage: number, over?: Partial<StockPile>): StockPile {
  return { item: r.cls, name: r.name, fluid: r.fluid, spendable: storage, carried: 0, storage, depot: 0, buffers: 0, crates: 0, ...over };
}

function place(id: string, over: Partial<StockPlace>): StockPlace {
  return {
    source: "storage",
    kind: "solid",
    instance_leaf: id,
    cls: "Build_StorageContainerMk1_C",
    name: "Storage Container",
    x_m: 0,
    y_m: 0,
    z_m: 0,
    region: { name: "Northern Forest", confidence: "interior", accuracy_m: 0, certain: true, text: "" },
    distance_m: 100,
    items: [],
    total: 0,
    slots: 24,
    slots_used: 0,
    fill: 0,
    capacity_m3: null,
    crate_kind: null,
    crate_kind_text: null,
    ...over,
  };
}

const plate = row("Iron Plate", "standard parts");
const ore = row("Iron Ore", "raw");
const water = row("Water", "fluids");
const shard = row("Power Shard", "boosts", { cls: "Desc_CrystalShard_C" });
const rifle = row("Rifle Ammo", "other");
const motor = row("Motor", "industrial parts", { locked: true });

describe("the Items address", () => {
  it("defaults to held and drops what a view cannot carry", () => {
    expect(itemsAddress("")).toEqual({ view: "held", item: "", box: "", rec: "", locked: false });
    expect(itemsAddress("?view=containers&item=x&box=12")).toEqual({ view: "containers", item: "", box: "12", rec: "", locked: false });
    expect(itemsAddress("?view=all&item=rotor&rec=alternate-steel-rotor&locked=1")).toEqual({ view: "all", item: "rotor", box: "", rec: "alternate-steel-rotor", locked: true });
    expect(itemsAddress("?view=held&locked=1").locked).toBe(false);
  });

  it("writes back what it reads", () => {
    const a = { view: "all" as const, item: "iron-plate", box: "", rec: "iron-plate", locked: true };
    expect(itemsAddress(itemsDash(a).slice(5))).toEqual(a);
    expect(itemsDash({ view: "containers", box: "7", item: "x" })).toBe("items?view=containers&box=7");
  });

  it("keys a name by its slug", () => {
    expect(slug("Reinforced Iron Plate")).toBe("reinforced-iron-plate");
    expect(slug("Factory Cart™")).toBe("factory-cart");
  });
});

describe("the item index", () => {
  it("joins held piles onto the catalogue and finds by slug or class", () => {
    const index = buildIndex([plate, ore], [pile(plate, 50, { buffers: 5, crates: 2 }), pile(row("Odd Thing", "other"), 3)]);
    expect(index.items.map((i) => i.name)).toEqual(["Iron Plate", "Iron Ore", "Odd Thing"]);
    expect(findItem(index, "iron-plate")!.held).toBe(57);
    expect(findItem(index, plate.cls)!.key).toBe("iron-plate");
    expect(findItem(index, "nope")).toBeNull();
  });

  it("groups held items by category, merging parts, raw and other for count and name", () => {
    const index = buildIndex([plate, ore, water, shard, rifle], [pile(plate, 5), pile(ore, 9), pile(water, 1), pile(shard, 2), pile(rifle, 3)]);
    expect(groupItems(index.items, "held", "category").map((g) => g.key)).toEqual(["parts", "raw", "fluids", "boosts", "other"]);
    const byCount = groupItems(index.items, "held", "count");
    expect(byCount.map((g) => g.key)).toEqual(["items", "fluids", "boosts"]);
    expect(byCount[0]!.rows.map((i) => i.name)).toEqual(["Iron Ore", "Iron Plate", "Rifle Ammo"]);
    expect(heldGroup("ingots")).toBe("parts");
  });

  it("hides a locked item from the catalogue unless shown or offered by a drive", () => {
    const index = buildIndex([motor], []);
    const it = index.items[0]!;
    const none = driveOffers(null);
    expect(shownInCatalogue(it, false, none)).toBe(false);
    expect(shownInCatalogue(it, true, none)).toBe(true);
    const drives = {
      spare: 0,
      last_used: null,
      drives: [{ hard_drive_id: 4, rerolls_left: 0, options: [{ schematic: "S", name: "Alternate: Rigor Motor", slots: 0, recipes: [{ cls: "Recipe_Alternate_Motor_1_C", name: "Rigor Motor", machine: "Manufacturer", products: [{ item: motor.cls, name: "Motor", amount: 1 }] }] }] }],
    } as HardDrivesResponse;
    const offers = driveOffers(drives);
    expect(offers.recipes["Recipe_Alternate_Motor_1_C"]).toBe("4");
    expect(shownInCatalogue(it, false, offers)).toBe(true);
    expect(shownRecipe("Recipe_Alternate_Motor_1_C", false, false, offers)).toBe(true);
    expect(shownRecipe("Recipe_Other_C", false, false, offers)).toBe(false);
    expect(shownRecipe("Recipe_Other_C", null, false, offers)).toBe(true);
  });

  it("writes slot counts compactly", () => {
    expect([compact(129), compact(3949), compact(70100), compact(1234567)]).toEqual(["129", "3.9k", "70k", "1.2M"]);
  });
});

describe("containers", () => {
  const box = place("1", { fill: 0.5, items: [{ item: plate.cls, name: "Iron Plate", amount: 40 }] });
  const full = place("2", { fill: 1, region: null, x_m: 5, items: [{ item: plate.cls, name: "Iron Plate", amount: 100 }] });
  const tank = place("3", { kind: "fluid", fill: 0.2, name: "Fluid Buffer" });
  const crate = place("4", { source: "crate", crate_kind: "death", fill: 1 });

  it("names crates by kind and gives them no fill", () => {
    expect(boxName(crate)).toBe("death crate");
    expect(boxFill(crate)).toBeNull();
    expect(boxFill(box)).toBe(0.5);
  });

  it("groups storage, fluid buffers and crates, fullest first; or by region", () => {
    const g = groupBoxes([box, tank, crate, full], "category");
    expect(g.map((x) => x.key)).toEqual(["storage", "fluid buffers", "crates"]);
    expect(g[0]!.rows.map((p) => p.instance_leaf)).toEqual(["2", "1"]);
    expect(groupBoxes([box, full], "region").map((x) => x.key)).toEqual(["Northern Forest", "off the map"]);
  });

  it("lists the places holding an item, biggest first", () => {
    expect(placesOf([box, full, tank], plate.cls).map((x) => x.amount)).toEqual([100, 40]);
  });
});
