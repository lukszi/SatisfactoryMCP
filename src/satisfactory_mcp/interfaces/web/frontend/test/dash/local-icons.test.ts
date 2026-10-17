import { describe, expect, it } from "vitest";

import { takeLocalChoices } from "../../src/dash/factories/local-icons";

function fake(initial: Record<string, string>) {
  const data = { ...initial };
  return {
    data,
    getItem: (k: string) => (k in data ? data[k]! : null),
    setItem: (k: string, v: string) => void (data[k] = v),
    removeItem: (k: string) => void delete data[k],
  };
}

describe("icon choices kept in the browser", () => {
  it("hands over one world's choices once and keeps the other worlds", () => {
    const store = fake({ k: JSON.stringify({ w1: { Plates: "Desc_IronPlate_C", Blank: "", Bad: 3 }, w2: { X: "Desc_X_C" } }) });
    expect(takeLocalChoices(store, "k", "w1")).toEqual({ Plates: "Desc_IronPlate_C", Blank: "" });
    expect(takeLocalChoices(store, "k", "w1")).toEqual({});
    expect(JSON.parse(store.data.k!)).toEqual({ w2: { X: "Desc_X_C" } });
    expect(takeLocalChoices(store, "k", "w2")).toEqual({ X: "Desc_X_C" });
    expect("k" in store.data).toBe(false);
  });

  it("reads nothing from a missing store, key or broken value", () => {
    expect(takeLocalChoices(null, "k", "w")).toEqual({});
    expect(takeLocalChoices(fake({}), "k", "w")).toEqual({});
    expect(takeLocalChoices(fake({ k: "{not json" }), "k", "w")).toEqual({});
  });
});
