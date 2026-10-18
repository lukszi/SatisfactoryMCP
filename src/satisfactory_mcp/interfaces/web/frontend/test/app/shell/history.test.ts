import { describe, expect, it } from "vitest";

import { arrivalOf, closeMove, crumbMove, stampOf, type Stamp } from "../../../src/app/shell/history";

const fromList: Stamp = { nav: "v.2", from: "factories", scroll: 0 };
const firstOfVisit: Stamp = { nav: "v.1", from: null, scroll: 0 };
const fromFiltered: Stamp = { nav: "v.3", from: "items?view=all&locked=1", scroll: 0 };

describe("the history rules", () => {
  it.each([
    ["opened from the parent", fromList, "factories", "back", "back"],
    ["opened from elsewhere", fromList, "power", "replace", "push"],
    ["a fresh deep link", firstOfVisit, "factories", "replace", "push"],
    ["an entry the site never stamped", null, "factories", "replace", "push"],
    ["the filter changed since opening", fromFiltered, "items?view=all", "replace", "push"],
  ] as const)("%s: close %s, crumb %s", (_what, stamp, parent, close, crumb) => {
    expect(closeMove(stamp, parent)).toBe(close);
    expect(crumbMove(stamp, parent)).toBe(crumb);
  });

  it.each([
    [null, "v.1", "fresh"],
    [fromList, "v.2", "own"],
    [fromList, "v.9", "traverse"],
  ] as const)("arrival onto %o with %s on screen is %s", (stamp, current, kind) => {
    expect(arrivalOf(stamp, current)).toBe(kind);
  });

  it("reads only its own stamps", () => {
    expect(stampOf(null)).toBeNull();
    expect(stampOf({ other: 1 })).toBeNull();
    expect(stampOf({ nav: "v.1" })).toEqual({ nav: "v.1", from: null, scroll: 0 });
    expect(stampOf(fromList)).toEqual(fromList);
  });
});
