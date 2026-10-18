import { describe, expect, it } from "vitest";

import { circuitUncounted, GENERATOR_UNWIRED, problemsOf, utilScale, worldUncounted, worstFirst } from "../../src/dash/power/model";
import { supplyBlock, utilBar } from "../../src/dash/power/supply";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, StarvedGenerator } from "../../src/api/shapes";

function ledger(over: Partial<Ledger>): Ledger {
  const base: Ledger = {
    generation_mw: 100,
    starved_generation_mw: 0,
    capacity_mw: 100,
    draw_mw: 50,
    measured_draw_mw: 40,
    headroom_mw: 50,
    measured_headroom_mw: 60,
    utilisation: 0.8,
    monitored: 5,
    unmonitored: 0,
    paused: 0,
    biomass_mw: 0,
    biomass_generators: 0,
  };
  const out = { ...base, ...over };
  out.capacity_mw = out.generation_mw - out.starved_generation_mw;
  out.headroom_mw = out.capacity_mw - out.draw_mw;
  out.measured_headroom_mw = out.capacity_mw - out.measured_draw_mw;
  return out;
}

function circuit(index: number, over: Partial<Ledger>, extra?: Partial<CircuitRow>): CircuitRow {
  return {
    index,
    ledger: ledger(over),
    generators: [],
    starved: [],
    unmodellable: [],
    consumers: 3,
    poles: 2,
    factories: [],
    factory_count: 0,
    centroid_m: null,
    bbox_m: null,
    ...extra,
  };
}

function ref(instance: string, name: string, place: Partial<MachineRef>): MachineRef {
  return { instance, name, circuit: null, factory: null, region: null, icon: null, x_m: null, y_m: null, ...place };
}

function starved(instance: string, circuit: number): StarvedGenerator {
  return { instance, name: "Coal Generator", mw: 75, missing: [], cause: "no fuel loaded", circuit, factory: null, region: null, icon: null, x_m: 1, y_m: 2 };
}

function response(over: Partial<CircuitsResponse>): CircuitsResponse {
  return {
    world: ledger({}),
    paused: 0,
    generators: [],
    starved: [],
    unmodellable: [],
    circuits: [],
    off_grid: { consumers: 0, paused: 0, draw_mw: 0, generators: 0, generation_mw: 0 },
    unwired: [],
    unwired_generators: [],
    no_generator: [],
    ...over,
  };
}

describe("power problems", () => {
  const data = response({
    unwired: [ref("a1", "Assembler", { factory: "Rotors" }), ref("a2", "Assembler", { factory: "Rotors" }), ref("c1", "Constructor", {})],
    no_generator: [ref("s1", "Smelter", { circuit: 1 })],
    starved: [starved("g1", 0)],
    unwired_generators: [ref("g9", "Coal Generator", {}), ref("g8", "Coal Generator", {}), ref("g7", "Coal Generator", {})],
  });

  it("groups one building with one fault at one place, worst kinds first and on no wire last", () => {
    const problems = problemsOf(data);
    expect(problems.map((p) => [p.kind, p.building, p.refs.length])).toEqual([
      ["no wire", "Assembler", 2],
      ["no wire", "Constructor", 1],
      ["no generator", "Smelter", 1],
      ["starved generator", "Coal Generator", 1],
      [GENERATOR_UNWIRED, "Coal Generator", 3],
    ]);
    expect(problems[0]!.where).toEqual({ where: "Rotors", dash: "factories/Rotors" });
    expect(problems[3]!.cause).toBe("no fuel loaded");
    expect(problems[4]!.fine).toBe(true);
  });

  it("keeps a circuit's own problems only", () => {
    expect(problemsOf(data, 1).map((p) => p.building)).toEqual(["Smelter"]);
    expect(problemsOf(data, 0).map((p) => p.kind)).toEqual(["starved generator"]);
  });
});

describe("circuits", () => {
  it("puts no generator, short now, starved, then least headroom per MW first", () => {
    const rows = [
      circuit(0, { generation_mw: 100, measured_draw_mw: 10, draw_mw: 10 }),
      circuit(1, { generation_mw: 100, measured_draw_mw: 90, draw_mw: 90 }),
      circuit(2, { generation_mw: 100, measured_draw_mw: 10, draw_mw: 10 }, { starved: [starved("g", 2)] }),
      circuit(3, { generation_mw: 50, measured_draw_mw: 60, draw_mw: 60 }),
      circuit(4, { generation_mw: 0, measured_draw_mw: 5, draw_mw: 5 }),
    ];
    expect(worstFirst(rows).map((r) => r.index)).toEqual([4, 3, 2, 1, 0]);
  });

  it("scales the bars to the most overdrawn circuit, between 1 and 1.5", () => {
    expect(utilScale([circuit(0, { generation_mw: 100, draw_mw: 50 })])).toBe(1);
    expect(utilScale([circuit(0, { generation_mw: 100, draw_mw: 121 })])).toBe(1.3);
    expect(utilScale([circuit(0, { generation_mw: 100, draw_mw: 400 })])).toBe(1.5);
  });

  it("marks draw above generation as over", () => {
    const bar = utilBar(circuit(0, { generation_mw: 100, draw_mw: 120, measured_draw_mw: 80 }), 1.2, false).safeHtml;
    expect(bar).toContain('class="np ov"');
    expect(bar).not.toContain('class="ms ov"');
    expect(utilBar(circuit(0, { generation_mw: 0 }), 1, false).safeHtml).toContain('title="no generator"');
  });
});

describe("supply block", () => {
  it("hatches the starved part of each generator type and draws the generation line", () => {
    const gens = [{ name: "Coal Generator", count: 4, mw: 300, starved_mw: 75, icon: null }];
    const out = supplyBlock({ gens, gen: 300, np: 360, ms: 200 }, [["3 paused", "tip"]]).safeHtml;
    expect(out).toContain('<i class="ha" style="flex:75"></i>');
    expect(out).toContain('class="gl"');
    expect(out).toContain('class="v bad-t"');
    expect(out).toContain("not counted");
  });

  it("draws no line and no overdraw when generation is unknown", () => {
    const out = supplyBlock({ gens: [], gen: 0, np: 50, ms: 40, unknown: true }, []).safeHtml;
    expect(out).not.toContain('class="gl"');
    expect(out).not.toContain(" ov");
    expect(out).toContain("no generator");
  });

  it("lists what the figures leave out", () => {
    const data = response({
      world: ledger({ biomass_mw: 120, biomass_generators: 4, unmonitored: 41, starved_generation_mw: 75 }),
      paused: 3,
      off_grid: { consumers: 2, paused: 0, draw_mw: 8, generators: 2, generation_mw: 150 },
    });
    expect(worldUncounted(data).map((u) => u[0])).toEqual(["biomass +120 MW", "75 MW starved", "41 unmeasured", "3 paused", "4 on no wire"]);
    expect(circuitUncounted(circuit(0, {}, { unmodellable: ["Build_X_C"] })).map((u) => u[0])).toEqual(["1 generator type unrated"]);
  });
});
