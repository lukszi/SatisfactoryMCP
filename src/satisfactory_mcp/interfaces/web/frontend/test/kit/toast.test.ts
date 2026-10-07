import { describe, expect, it } from "vitest";

import { friendlyError, withoutToolHints } from "../../src/kit/toast";

describe("friendlyError", () => {
  it("translates the browser's network and parse phrases", () => {
    expect(friendlyError(new TypeError("Failed to fetch"))).toBe("the server is not answering; is it still running?");
    expect(friendlyError({ message: "NetworkError when attempting to fetch resource." })).toContain("not answering");
    expect(friendlyError(new SyntaxError("Unexpected token < in JSON"))).toBe(
      "the server answered with something that is not JSON"
    );
  });

  it("says an HTTP failure without the path", () => {
    expect(friendlyError("422 /api/nodes?near=x")).toBe("a value in the address is out of range");
    expect(friendlyError("500 /api/power")).toBe("the server hit an error");
  });

  it("keeps a file name and drops the folders it lives in", () => {
    expect(friendlyError(new Error("cannot read 'C:\\Users\\someone\\saves\\my world.sav'"))).toBe(
      "cannot read 'my world.sav'"
    );
    expect(friendlyError(new Error("missing /home/someone/saves/a.sav: gone"))).toBe("missing a.sav: gone");
  });

  it("drops an API path out of a sentence", () => {
    expect(friendlyError(new Error("no world at /api/worlds/abc, sorry"))).toBe("no world at, sorry");
  });

  it("falls back to a plain sentence when nothing is left", () => {
    expect(friendlyError(new Error("/api/x"))).toBe("the server hit an error");
    expect(friendlyError(null)).toBe("null");
  });
});

describe("withoutToolHints", () => {
  it("cuts the clauses that name an MCP tool, keeping the first", () => {
    expect(withoutToolHints("! no such factory; try list_factories")).toBe("no such factory");
    expect(withoutToolHints("no node here, or use search_resource_nodes")).toBe("no node here");
    expect(withoutToolHints("too far -- plan_factory can do it")).toBe("too far");
    expect(withoutToolHints("busy; try again")).toBe("busy; try again");
  });

  it("rewrites an unknown place into what the search box accepts", () => {
    expect(withoutToolHints("'Atlantis' does not name a place, see describe_location")).toBe(
      "“Atlantis” is not a place: try me, x,y, a factory name or node:…"
    );
  });
});
