import { beforeEach, describe, expect, it, vi } from "vitest";

/* palette.ts keeps its table in module state and audits once, so every test loads it afresh. */
async function freshPalette() {
  vi.resetModules();
  return (await import("../../src/map/palette")).declareColours;
}

const auditRan = () => Promise.resolve();

let errors: string[];

beforeEach(() => {
  errors = [];
  vi.spyOn(console, "error").mockImplementation((message: string) => {
    errors.push(message);
  });
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

const about = (pair: string) => errors.filter((message) => message.includes(pair));

describe("declareColours", () => {
  it("hands its argument straight back", async () => {
    const declareColours = await freshPalette();
    const colours = { a: "#112233" };
    expect(declareColours("owner", colours)).toBe(colours);
  });

  it("refuses a colour it cannot compare, and a name declared twice", async () => {
    const declareColours = await freshPalette();
    declareColours("crates", { short: "#fff", named: "red" });
    declareColours("crates", { short: "#ffffff" });
    expect(errors).toContain('crates/short is "#fff", not a #rrggbb — it is not compared');
    expect(errors).toContain('crates/named is "red", not a #rrggbb — it is not compared');
    expect(errors).toContain("crates/short is declared twice");
  });

  it("does nothing at all in a production build", async () => {
    vi.stubEnv("DEV", false);
    const declareColours = await freshPalette();
    declareColours("crates", { short: "#fff" });
    await auditRan();
    expect(errors).toEqual([]);
  });
});

describe("the audit", () => {
  it("calls out two owners whose colours cannot be told apart", async () => {
    const declareColours = await freshPalette();
    declareColours("belts", { steel: "#808080" });
    declareColours("pipes", { iron: "#828282" });
    await auditRan();
    expect(about("belts/steel <-> pipes/iron")).toEqual([
      expect.stringMatching(/^palette: belts\/steel <-> pipes\/iron is dE 0\.\d, under 15/),
    ]);
  });

  it("lets one owner's family stay close, and far colours pass", async () => {
    const declareColours = await freshPalette();
    declareColours("belts", { slow: "#808080", fast: "#828282" });
    declareColours("pipes", { white: "#ffffff" });
    await auditRan();
    expect(about("belts/slow <-> belts/fast")).toEqual([]);
    expect(errors.filter((message) => message.includes("under 15"))).toEqual([]);
  });

  it("measures CIE76 to one decimal, matching a listed warrant exactly", async () => {
    const declareColours = await freshPalette();
    declareColours("markers", { Desc_Water_C: "#3f8fd0" });
    declareColours("placements", { machines: "#4aa3df" });
    await auditRan();
    expect(about("markers/Desc_Water_C <-> placements/machines")).toEqual([]);
  });

  it("flags a listed pair whose distance moved, however far", async () => {
    const declareColours = await freshPalette();
    declareColours("markers", { Desc_Water_C: "#3f8fd0", Desc_Coal_C: "#000000" });
    declareColours("placements", { machines: "#4aa0df" });
    declareColours("regions", { A: "#ffffff" });
    await auditRan();
    expect(about("markers/Desc_Water_C <-> placements/machines")[0]).toMatch(/is dE \d+(\.\d)?, listed at 8\.6/);
    expect(about("markers/Desc_Coal_C <-> regions/A")[0]).toMatch(/is dE 100, listed at 6\.3/);
  });

  it("reports a listed pair whose colours are no longer declared", async () => {
    const declareColours = await freshPalette();
    declareColours("crates", { x: "#123456" });
    await auditRan();
    expect(about("markers/Desc_OreIron_C <-> routes/chevrons")[0]).toMatch(/outlived its subject/);
  });

  it("runs once, after every module has declared", async () => {
    const declareColours = await freshPalette();
    declareColours("belts", { steel: "#808080" });
    declareColours("pipes", { iron: "#808080" });
    await auditRan();
    await auditRan();
    expect(about("belts/steel <-> pipes/iron")).toHaveLength(1);
  });
});
