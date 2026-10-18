/* How the map studio names things: a type's title, an input, a job's options. Pure. */

import type { MapJobBody, MapTypeBody } from "../../api/shapes";

/** The server's title without the trailing " ★": the "default" chip says it. */
export function typeTitle(row: MapTypeBody): string {
  return row.title.replace(/\s*★$/, "");
}

/** The job preset that rebuilds each input, and what the input is called in a sentence. */
export const INPUT_PRESET: Record<string, string> = { heightfield: "heightmap", caves: "caves", rocks: "rocks", paint: "paint" };

const INPUT_WORDS: Record<string, string> = { heightmap: "heightfield", caves: "cave masks", rocks: "rock collision", paint: "paint layers" };

export function inputWords(preset: string): string {
  return INPUT_WORDS[preset] || preset;
}

/** "render · 8,192 px · live sun": what a job was asked to make. */
export function jobOptionsText(job: MapJobBody): string {
  const options = job.options as Record<string, unknown>;
  if (job.preset === "render") {
    const parts = ["render", Number(options.size).toLocaleString("en-US") + " px"];
    if (options.recipe === "kernel-only") parts.push("kernel only");
    if (options.top === false) parts.push("no arches");
    if (options.titan_trees === false) parts.push("no Titan trees");
    if (options.keep_cache) parts.push("keeps the raster cache");
    if (options.restyle) parts.push("palette only");
    if (options.light) parts.push("live sun");
    return parts.join(" · ");
  }
  if (job.preset === "artwork") return options.enhance ? "artwork · upscaled" : "artwork";
  return "input";
}
