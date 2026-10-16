import { describe, expect, it } from "vitest";

import { redirectFor, routeOf, scrollKey, settingsSection } from "../../../src/app/shell/router";

describe("the route table", () => {
  it("reads page, subject and the lit tab", () => {
    expect(routeOf("")).toEqual({ page: "map", subject: "", top: "map" });
    expect(routeOf("factories/Iron Works")).toEqual({ page: "factories", subject: "Iron Works", top: "factories" });
    expect(routeOf("factories/Iron Works?floor=2")).toEqual({ page: "factories", subject: "Iron Works?floor=2", top: "factories" });
    expect(routeOf("planner/p1/track")).toEqual({ page: "planner", subject: "p1/track", top: "planner" });
    expect(routeOf("items?view=all&item=rotor")).toEqual({ page: "items", subject: "?view=all&item=rotor", top: "items" });
    expect(routeOf("items").top).toBe("items");
    expect(routeOf("maps").top).toBe("map");
    expect(routeOf("kit").top).toBe("");
  });

  it("sends an unknown page to the overview", () => {
    expect(routeOf("nowhere/x")).toEqual({ page: "overview", subject: "", top: "overview" });
    expect(routeOf("map")).toEqual({ page: "overview", subject: "", top: "overview" });
  });

  it("redirects the old settings tab", () => {
    expect(redirectFor("settings")).toEqual({ dash: null, settings: "" });
    expect(redirectFor("settings/general")).toEqual({ dash: null, settings: "" });
    expect(redirectFor("settings/maps")).toEqual({ dash: "maps" });
    expect(redirectFor("overview")).toBeNull();
    expect(redirectFor("items")).toBeNull();
  });

  it("lands the old Inventory and Recipes addresses on Items", () => {
    expect(redirectFor("inventory")).toEqual({ dash: "items?view=held" });
    expect(redirectFor("inventory/Quartz Crystal")).toEqual({ dash: "items?view=held&item=quartz-crystal" });
    expect(redirectFor("inventory-boosts")).toEqual({ dash: "items?view=held" });
    expect(redirectFor("recipes")).toEqual({ dash: "items?view=all" });
    expect(redirectFor("recipes/recipes?q=iron")).toEqual({ dash: "items?view=all" });
    expect(redirectFor("recipes/item/Desc_IronPlate_C")).toEqual({ dash: "items?view=all&item=Desc_IronPlate_C" });
    expect(redirectFor("recipes/recipe/Recipe_IronPlate_C")).toEqual({ dash: "items?view=all&rec=Recipe_IronPlate_C" });
    expect(redirectFor("item-iron-plate")).toEqual({ dash: "items?view=all&item=iron-plate" });
    expect(redirectFor("recipe-iron-plate")).toEqual({ dash: "items?view=all&rec=iron-plate" });
  });

  it("lands the old World views on the one World page", () => {
    expect(redirectFor("world/nodes?near=me")).toEqual({ dash: "world" });
    expect(redirectFor("world/pickups?view=collected")).toEqual({ dash: "world?show=pickups&mode=collected" });
    expect(redirectFor("world")).toBeNull();
    expect(redirectFor("world?show=conduits")).toBeNull();
    expect(scrollKey("world?show=pickups&sel=p-1")).toBe(scrollKey("world?show=pickups"));
  });

  it("keeps the scroll while picking on Items, not across views or pages", () => {
    expect(scrollKey("items?view=held&item=rotor")).toBe(scrollKey("items?view=held"));
    expect(scrollKey("items?view=all")).not.toBe(scrollKey("items?view=held"));
    expect(scrollKey("factories/A")).not.toBe(scrollKey("factories/B"));
  });

  it("reads the dialog's section from the address", () => {
    const sections = ["spoilers", "map"];
    expect(settingsSection(undefined, sections)).toBeNull();
    expect(settingsSection("", sections)).toBe("");
    expect(settingsSection("map", sections)).toBe("map");
    expect(settingsSection("bogus", sections)).toBe("");
  });
});
