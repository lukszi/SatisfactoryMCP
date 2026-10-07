import { describe, expect, it, vi } from "vitest";

import type { WorldRow } from "../../src/api/shapes";

/* state.ts reads the address bar while it is imported, so each boot loads it afresh. */
async function boot(hash: string) {
  vi.resetModules();
  vi.stubGlobal("location", { hash: hash });
  return import("../../src/app/state");
}

function world(id: string, saves: [string, string][]): WorldRow {
  return {
    world_id: id,
    session_name: id,
    mtime: 0,
    newest_filename: "",
    play_duration_s: 0,
    saves: saves.map(([filename, path]) => ({ filename, path, session_name: id, play_duration_s: 0, mtime_ns: 0 })),
  };
}

describe("parseHash", async () => {
  const { parseHash } = await boot("");

  it("decodes each key once and skips pieces that are not pairs", () => {
    expect(parseHash("#world=w1&save=a%20b.sav&z=2&&=x&loose")).toEqual({ world: "w1", save: "a b.sav", z: "2" });
  });

  it("reads a broken escape as best it can and says which key", () => {
    const garbled: string[] = [];
    expect(parseHash("#world=100%&save=%zz%41", garbled)).toEqual({ world: "100%", save: "%zzA" });
    expect(garbled).toEqual(["world", "save"]);
  });

  it("keeps a dashboard query's own parameters with the dashboard", () => {
    expect(parseHash("#dash=world/nodes?near=x&kind=coal&z=1")).toEqual({ dash: "world/nodes?near=x&kind=coal", z: "1" });
    expect(parseHash("#dash=power&kind=coal")).toEqual({ dash: "power", kind: "coal" });
  });
});

describe("the boot address", () => {
  it("opens the overview unless the link is about the map", async () => {
    expect((await boot("")).state.dash).toBe("overview");
    expect((await boot("#z=3&c=1,2")).state.dash).toBe("");
    expect((await boot("#dash=power&z=3")).state.dash).toBe("power");
  });

  it("reads the ticked pickups as a sorted list", async () => {
    const { BOOT, state, parseList } = await boot("#pickups=slug,,pod");
    expect(BOOT.pickups).toBe("slug,,pod");
    expect(state.pickups).toEqual(["pod", "slug"]);
    expect(parseList(undefined)).toEqual([]);
  });

  it("remembers a garbled boot link for the note", async () => {
    const { BOOT_GARBLED, garbledNote } = await boot("#world=%&save=%");
    expect(BOOT_GARBLED).toEqual(["world", "save"]);
    expect(garbledNote(BOOT_GARBLED)).toBe("the link has a broken % escape in “world”, “save”; it was read as best it could be");
  });

  it("counts an event from just before the page opened as since", async () => {
    const { isSincePageOpened, state } = await boot("");
    state.openedAtMs = 100_000;
    expect(isSincePageOpened(99)).toBe(true);
    expect(isSincePageOpened(97.9)).toBe(false);
  });
});

describe("the pinned save", () => {
  it("converts between a fragment's filename and the pin's path", async () => {
    const { currentWorld, pinnedFilename, pinnedPath, state } = await boot("");
    const w = world("w1", [
      ["a.sav", "C:/saves/a.sav"],
      ["b.sav", ""],
    ]);
    state.worlds = [world("w0", []), w];
    expect(currentWorld()).toBeNull();
    expect(pinnedFilename()).toBe("");
    state.world = "w1";
    expect(currentWorld()).toBe(w);
    state.save = "C:/saves/a.sav";
    expect(pinnedFilename()).toBe("a.sav");
    state.save = "b.sav";
    expect(pinnedFilename()).toBe("b.sav");
    expect(pinnedPath("a.sav", w)).toBe("C:/saves/a.sav");
    expect(pinnedPath("b.sav", w)).toBe("b.sav");
    expect(pinnedPath("c.sav", w)).toBe("");
    expect(pinnedPath("a.sav", null)).toBe("");
  });
});
