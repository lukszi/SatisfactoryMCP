import { describe, expect, it, vi } from "vitest";

import { createListeners } from "../../src/app/listeners";

import type { ApiUrl } from "../../src/api/client";
import type { Wave } from "../../src/app/registry";

/* registry.ts keeps its list in module state, so each test starts from an empty one. */
async function freshRegistry() {
  vi.resetModules();
  return import("../../src/app/registry");
}

function fetcher(path: ApiUrl, wave: Wave, rank: number) {
  return { wave: wave, rank: rank, path: path, label: path, clears: [], refilters: false, draw: () => {} };
}

describe("the fetch registry", () => {
  it("issues a wave in rank order, whatever order the modules registered in", async () => {
    const registry = await freshRegistry();
    registry.registerFetch(fetcher("/api/belts", "static", 30));
    registry.registerFetch(fetcher("/api/machines", "live", 10));
    registry.registerFetch(fetcher("/api/nodes", "static", 10));
    expect(registry.fetchersOf("static").map((f) => f.path)).toEqual(["/api/nodes", "/api/belts"]);
    expect(registry.fetchersOf("live").map((f) => f.path)).toEqual(["/api/machines"]);
  });

  it("hands out a copy the caller may walk while draws register more", async () => {
    const registry = await freshRegistry();
    registry.registerFetch(fetcher("/api/nodes", "static", 10));
    const wave = registry.fetchersOf("static");
    registry.registerFetch(fetcher("/api/belts", "static", 20));
    expect(wave).toHaveLength(1);
  });

  it("finds one fetch by its path", async () => {
    const registry = await freshRegistry();
    const nodes = fetcher("/api/nodes", "static", 10);
    registry.registerFetch(nodes);
    expect(registry.fetcherFor("/api/nodes")).toBe(nodes);
    expect(registry.fetcherFor("/api/belts")).toBeUndefined();
  });

  it("says out loud in dev when two features register one path", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const registry = await freshRegistry();
    registry.registerFetch(fetcher("/api/summary", "live", 10));
    expect(error).not.toHaveBeenCalled();
    registry.registerFetch(fetcher("/api/summary", "live", 20));
    expect(error).toHaveBeenCalledWith("two fetchers registered for /api/summary — that is two requests");
  });
});

describe("createListeners", () => {
  it("calls every listener with the emitted arguments, in the order added", () => {
    const listeners = createListeners<[string, number]>();
    const heard: string[] = [];
    listeners.on((name, n) => heard.push("first " + name + n));
    listeners.on((name, n) => heard.push("second " + name + n));
    listeners.emit("x", 1);
    expect(heard).toEqual(["first x1", "second x1"]);
  });
});
