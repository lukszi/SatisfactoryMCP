import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ageShort,
  amount,
  buildingCounts,
  byCodeUnit,
  bytes,
  coords,
  count,
  countRange,
  duration,
  flow,
  formatNumber,
  isoDate,
  joinWithConjunction,
  metres,
  mw,
  nowSeconds,
  pct,
  perMin,
  phaseText,
  regionLine,
  roundHalfEven,
  shortResource,
  signed,
  tallyBy,
  timeOfDay,
  withDetail,
  withUnit,
} from "../../src/kit/format";

import type { Region } from "../../src/api/shapes";

describe("numbers", () => {
  it("groups thousands", () => {
    expect(count(24000)).toBe("24,000");
    expect(count(1234567.5)).toBe("1,234,567.5");
  });

  it("rounds a half to even, on the exact decimal expansion", () => {
    expect(roundHalfEven(472.5)).toBe(472);
    expect(roundHalfEven(473.5)).toBe(474);
    expect(roundHalfEven(-17.5)).toBe(-18);
    expect(roundHalfEven(0.25, 1)).toBe(0.2);
    expect(roundHalfEven(0.35, 1)).toBe(0.3);
    expect(roundHalfEven(2.675, 2)).toBe(2.67);
    expect(roundHalfEven(1.2501, 1)).toBe(1.3);
    expect(Object.is(roundHalfEven(-0.4), 0)).toBe(true);
  });

  it("hands huge and non-finite values to Math.round", () => {
    expect(roundHalfEven(1e16 + 2)).toBe(Math.round(1e16 + 2));
    expect(roundHalfEven(Infinity)).toBe(Infinity);
    expect(roundHalfEven(NaN)).toBeNaN();
  });

  it("formats to one decimal unless told otherwise", () => {
    expect(formatNumber(60)).toBe("60");
    expect(formatNumber(1234.56)).toBe("1,234.6");
    expect(formatNumber(1234.5, 0)).toBe("1,234");
  });

  it("puts a unit after a value and a dash for none", () => {
    expect(withUnit(1.25, 1, " MW")).toBe("1.2 MW");
    expect(withUnit(null, 1, " MW")).toBe("–");
    expect(metres(696.5)).toBe("696 m");
    expect(metres(undefined)).toBe("–");
    expect(metres(12.34, 1)).toBe("12.3 m");
  });

  it("signs a magnitude only when it does not round to zero", () => {
    expect(signed(3, count)).toBe("+3");
    expect(signed(-3, count)).toBe("-3");
    expect(signed(0, count)).toBe("0");
  });

  it("says megawatts, signed on request", () => {
    expect(mw(1106.5)).toBe("1,107 MW");
    expect(mw(-1106.5)).toBe("-1,107 MW");
    expect(mw(1106.5, { signed: true })).toBe("+1,107 MW");
    expect(mw(-0.4, { signed: true })).toBe("0 MW");
  });

  it("spells ranges, amounts, rates and percentages", () => {
    expect(countRange(8, 27)).toBe("8–27");
    expect(countRange(3, 3)).toBe("3");
    expect(countRange(4, null)).toBe("4");
    expect(amount(12.34, true)).toBe("12.3 m³");
    expect(amount(12.5)).toBe("12");
    expect(perMin(12.34)).toBe("12.3/min");
    expect(perMin(5, false)).toBe("5");
    expect(flow("Iron Plate", 30)).toBe("30 Iron Plate/min");
    expect(pct(0.756)).toBe("76%");
    expect(pct(0.75641, 1)).toBe("75.6%");
    expect(pct(null)).toBe("–");
  });

  it("scales bytes and durations", () => {
    expect(bytes(null)).toBe("0 B");
    expect(bytes(999)).toBe("999 B");
    expect(bytes(1500)).toBe("2 kB");
    expect(bytes(2_500_000)).toBe("3 MB");
    expect(bytes(1.5e9)).toBe("1.5 GB");
    expect(duration(undefined)).toBe("0 s");
    expect(duration(-5)).toBe("0 s");
    expect(duration(89)).toBe("89 s");
    expect(duration(90)).toBe("2 min");
    expect(duration(5400)).toBe("1.5 h");
  });
});

describe("places and names", () => {
  it("shortens a resource class", () => {
    expect(shortResource("Desc_OreIron_C")).toBe("OreIron");
    expect(shortResource(null)).toBe("");
  });

  it("never drops the confidence word from a region", () => {
    const region = { name: "Northern Forest", confidence: "interior" } as Region;
    expect(regionLine(region)).toBe("Northern Forest, interior");
    expect(regionLine(null)).toBe("off the map");
  });

  it("reads a phase asset name as words", () => {
    expect(phaseText("GP_Project_Assembly_Phase_3")).toBe("Project Assembly phase 3");
    expect(phaseText("Tier 0")).toBe("Tier 0");
    expect(phaseText(null)).toBeNull();
  });

  it("prints whole-metre coordinates", () => {
    expect(coords(472.5, -961.5)).toBe("x 472, y -962 m");
    expect(coords(1140.2, -2821.7)).toBe("x 1,140, y -2,822 m");
  });

  it("adds a detail after a middle dot only when there is one", () => {
    expect(withDetail("Constructor", "Iron Plate")).toBe("Constructor · Iron Plate");
    expect(withDetail("Constructor", "")).toBe("Constructor");
    expect(withDetail("Constructor", null)).toBe("Constructor");
  });

  it("sorts in the code-unit order of a bare sort, whatever the locale", () => {
    const names = ["b", "Desc_Water_C", "a", "Desc_OreIron_C", "Z", "a"];
    expect([...names].sort(byCodeUnit)).toEqual([...names].sort());
    expect(byCodeUnit("a", "a")).toBe(0);
    expect(byCodeUnit("Z", "a")).toBe(-1);
  });

  it("joins names with a conjunction before the last", () => {
    expect(joinWithConjunction([], "and")).toBe("");
    expect(joinWithConjunction(["a"], "and")).toBe("a");
    expect(joinWithConjunction(["a", "b"], "or")).toBe("a or b");
    expect(joinWithConjunction(["a", "b", "c"], "and")).toBe("a, b and c");
  });

  it("lists building counts, limited and formatted on request", () => {
    const entries = [
      { count: 3, name: "Constructor" },
      { count: 1200, name: "Smelter" },
    ];
    expect(buildingCounts(entries)).toBe("3× Constructor, 1200× Smelter");
    expect(buildingCounts(entries, "; ", 1)).toBe("3× Constructor");
    expect(buildingCounts(entries, " + ", undefined, count)).toBe("3× Constructor + 1,200× Smelter");
  });

  it("tallies most first, ties in first-seen order", () => {
    const rows = ["b", "a", "c", "a"];
    expect(tallyBy(rows, (r) => r)).toEqual([
      { name: "a", count: 2 },
      { name: "b", count: 1 },
      { name: "c", count: 1 },
    ]);
  });
});

describe("times", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("says an age in its largest whole unit", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(1_000_000_000_000));
    const now = nowSeconds();
    expect(now).toBe(1_000_000_000);
    expect(ageShort(now - 30)).toBe("30s");
    expect(ageShort(now - 90)).toBe("2m");
    expect(ageShort(now - 7200)).toBe("2h");
    expect(ageShort(now - 2 * 86400)).toBe("2d");
    expect(ageShort(now + 60)).toBe("0s");
  });

  it("prints a local date and clock time", () => {
    const noon = Date.UTC(2026, 0, 5, 12) / 1000;
    expect(isoDate(noon)).toBe("2026-01-05");
    expect(isoDate(null)).toBe("–");
    expect(timeOfDay(noon)).toMatch(/^\d\d:\d\d$/);
  });
});
