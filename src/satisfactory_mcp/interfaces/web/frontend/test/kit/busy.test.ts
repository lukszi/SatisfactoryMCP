import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { busy } from "../../src/kit/busy";

function target() {
  const attrs: Record<string, string> = {};
  const classes = new Set<string>();
  return {
    attrs,
    classes,
    setAttribute: (name: string, value: string) => void (attrs[name] = value),
    removeAttribute: (name: string) => void delete attrs[name],
    classList: { add: (name: string) => void classes.add(name), remove: (name: string) => void classes.delete(name) },
  };
}

function after(ms: number) {
  return new Promise<string>((resolve) => setTimeout(() => resolve("done"), ms));
}

describe("the busy timing rule", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows nothing for work under 300 ms, but is aria-busy at once", async () => {
    const el = target();
    const result = busy(el, after(200));
    expect(el.attrs["aria-busy"]).toBe("true");
    await vi.advanceTimersByTimeAsync(201);
    expect(await result).toBe("done");
    expect(el.classes.has("is-busy")).toBe(false);
    expect(el.attrs["aria-busy"]).toBeUndefined();
  });

  it("once shown, holds the indicator at least 500 ms", async () => {
    const el = target();
    let settled = false;
    busy(el, after(400)).then(() => (settled = true));
    await vi.advanceTimersByTimeAsync(300);
    expect(el.classes.has("is-busy")).toBe(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(399);
    expect(el.classes.has("is-busy")).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(settled).toBe(true);
    expect(el.classes.has("is-busy")).toBe(false);
  });

  it("passes a failure through after clearing", async () => {
    const el = target();
    const failing = expect(busy(el, Promise.reject(new Error("no")))).rejects.toThrow("no");
    await vi.advanceTimersByTimeAsync(1);
    await failing;
    expect(el.attrs["aria-busy"]).toBeUndefined();
  });
});
