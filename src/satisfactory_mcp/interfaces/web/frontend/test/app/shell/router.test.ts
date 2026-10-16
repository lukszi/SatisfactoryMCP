import { describe, expect, it } from "vitest";

import { redirectFor, routeOf, settingsSection } from "../../../src/app/shell/router";

describe("the route table", () => {
  it("reads page, subject and the lit tab", () => {
    expect(routeOf("")).toEqual({ page: "map", subject: "", top: "map" });
    expect(routeOf("factories/Iron Works")).toEqual({ page: "factories", subject: "Iron Works", top: "factories" });
    expect(routeOf("planner/p1/track")).toEqual({ page: "planner", subject: "p1/track", top: "planner" });
    expect(routeOf("recipes/item/Desc_IronPlate_C").top).toBe("items");
    expect(routeOf("inventory").top).toBe("items");
    expect(routeOf("maps").top).toBe("map");
    expect(routeOf("kit").top).toBe("");
  });

  it("sends an unknown page to the overview", () => {
    expect(routeOf("nowhere/x")).toEqual({ page: "overview", subject: "", top: "overview" });
    expect(routeOf("map")).toEqual({ page: "overview", subject: "", top: "overview" });
  });

  it("redirects the old settings tab and the Items shorthand", () => {
    expect(redirectFor("settings")).toEqual({ dash: null, settings: "" });
    expect(redirectFor("settings/general")).toEqual({ dash: null, settings: "" });
    expect(redirectFor("settings/maps")).toEqual({ dash: "maps" });
    expect(redirectFor("items")).toEqual({ dash: "inventory" });
    expect(redirectFor("overview")).toBeNull();
  });

  it("reads the dialog's section from the address", () => {
    const sections = ["spoilers", "map"];
    expect(settingsSection(undefined, sections)).toBeNull();
    expect(settingsSection("", sections)).toBe("");
    expect(settingsSection("map", sections)).toBe("map");
    expect(settingsSection("bogus", sections)).toBe("");
  });
});
