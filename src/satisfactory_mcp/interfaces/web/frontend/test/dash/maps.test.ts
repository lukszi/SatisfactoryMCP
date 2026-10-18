import { describe, expect, it } from "vitest";

import { mapsAddress, mapsDash } from "../../src/dash/maps/address";
import { drawnFrom, jobTime, shownJobs } from "../../src/dash/maps/model";
import { jobOptionsText, typeTitle } from "../../src/dash/maps/words";

import type { MapJobBody, MapTypeBody } from "../../src/api/shapes";

function job(over: Partial<MapJobBody>): MapJobBody {
  return {
    id: "j",
    preset: "render",
    options: {},
    label: null,
    produces: [],
    replaces: null,
    status: "queued",
    created: 0,
    started: null,
    ended: null,
    stage: "",
    stage_words: "",
    pct: null,
    eta_s: null,
    elapsed_s: null,
    estimate_s: null,
    exit_code: null,
    peak_rss: null,
    error_line: null,
    last_line: null,
    ...over,
  };
}

describe("the map studio's address", () => {
  it("reads the preset and the picked type, dropping an unknown preset", () => {
    expect(mapsAddress("")).toEqual({ gen: "", type: "" });
    expect(mapsAddress("?gen=artwork&type=terrain-r7")).toEqual({ gen: "artwork", type: "terrain-r7" });
    expect(mapsAddress("?gen=nope")).toEqual({ gen: "", type: "" });
  });

  it("writes them back", () => {
    expect(mapsDash({ gen: "", type: "" })).toBe("maps");
    expect(mapsDash({ gen: "render", type: "a b" })).toBe("maps?gen=render&type=a%20b");
    expect(mapsAddress(mapsDash({ gen: "inputs", type: "x" }).slice(4))).toEqual({ gen: "inputs", type: "x" });
  });
});

describe("the jobs list", () => {
  it("shows the active jobs and the last one that ended in the past hour", () => {
    const jobs = [job({ id: "q" }), job({ id: "old", status: "done", ended: 100 }), job({ id: "new", status: "failed", ended: 3000 }), job({ id: "r", status: "running" })];
    expect(shownJobs(jobs, 3600).map((j) => j.id)).toEqual(["q", "r", "new"]);
    expect(shownJobs(jobs, 9000).map((j) => j.id)).toEqual(["q", "r"]);
  });

  it("says each job's time in one format", () => {
    expect(jobTime(job({ status: "running", eta_s: 1920 }))).toBe("32 min left");
    expect(jobTime(job({ status: "running" }))).toBe("");
    expect(jobTime(job({ estimate_s: 180 }))).toBe("3 min");
    expect(jobTime(job({ status: "done", started: 0, ended: 6120 }))).toBe("1 h 42 min");
  });

  it("names a render's options", () => {
    expect(jobOptionsText(job({ options: { size: 8192, light: true } }))).toBe("render · 8,192 px · live sun");
  });
});

describe("map types and inputs", () => {
  it("drops the default star from a title and counts the maps drawn from an input", () => {
    const types = [{ title: "Terrain · 15 Oct ★", axes: { inputs: { heightfield: {}, caves: {} } } }, { title: "Game map", axes: { inputs: { artwork_sheet: {} } } }, { title: "Old", axes: {} }] as unknown as MapTypeBody[];
    expect(typeTitle(types[0]!)).toBe("Terrain · 15 Oct");
    expect(drawnFrom("heightfield", types)).toBe(1);
    expect(drawnFrom("paint", types)).toBe(0);
  });
});
