/* The map studio's address, `dash=maps?gen=<preset>&type=<id>`: the generate form's preset
 * and the picked map type. Pure, so it is tested without a page. */

export type GenPreset = "render" | "artwork" | "inputs";

export const GEN_PRESETS: [GenPreset, string][] = [
  ["render", "render: a drawn style"],
  ["artwork", "artwork from the game"],
  ["inputs", "heightfield inputs"],
];

export interface MapsAddress {
  /** The open generate form's preset; "" while the form is closed. */
  gen: GenPreset | "";
  type: string;
}

/** `subject` is what follows "maps" in the dash: "" or "?gen=…&type=…". */
export function mapsAddress(subject: string): MapsAddress {
  const query = new URLSearchParams(subject.replace(/^[/?]+/, ""));
  const gen = query.get("gen") || "";
  return {
    gen: GEN_PRESETS.some((p) => p[0] === gen) ? (gen as GenPreset) : "",
    type: query.get("type") || "",
  };
}

export function mapsDash(a: MapsAddress): string {
  const pairs: string[] = [];
  if (a.gen) pairs.push("gen=" + a.gen);
  if (a.type) pairs.push("type=" + encodeURIComponent(a.type));
  return "maps" + (pairs.length ? "?" + pairs.join("&") : "");
}
