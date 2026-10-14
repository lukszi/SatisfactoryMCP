/* The shared map read from static files instead of the server: docs/map/sharing.md.
 *
 * data/site.json holds, per layer, what the z0 probe's headers would have said and where each
 * tile tree sits. Every path is relative, so the site works under any folder. */

export interface SiteLayer {
  headers: Record<string, string>;
  /** This layer's own trees (tiles, tiles@2x, unlit, trees), relative to the page. */
  dir: string;
  /** The shared light pyramid (nrm and hz), or "" for a layer exported without its light. */
  light: string;
  /** The file suffix of the unlit and trees tiles. */
  suffix: Record<string, string>;
  /** Base64 bitmap of the tree tiles that exist, in level order; absent when all do. */
  trees?: string;
}

export interface SiteManifest {
  layers: Record<string, SiteLayer>;
}

let site: SiteManifest = { layers: {} };
const treeBits: Record<string, Uint8Array> = {};

export function useSite(body: SiteManifest): void {
  site = body;
  Object.keys(body.layers).forEach(function (id) {
    const packed = body.layers[id]!.trees;
    if (packed !== undefined) treeBits[id] = Uint8Array.from(atob(packed), (c) => c.charCodeAt(0));
  });
}

export function loadSite(): Promise<void> {
  return fetch("data/site.json")
    .then(function (r) {
      if (!r.ok) throw new Error("data/site.json: " + r.status);
      return r.json() as Promise<SiteManifest>;
    })
    .then(useSite);
}

/** What the server's HEAD on 0/0/0 answered, rebuilt from the manifest. */
export function siteProbe(layer: string): Response {
  const row = site.layers[layer];
  if (!row) return new Response(null, { status: 204 });
  return new Response(null, { status: 200, headers: row.headers });
}

/** Whether a sparse tree tile exists: bit (4^z - 1) / 3 + y * 2^z + x of the layer's bitmap. */
export function hasTrees(layer: string, z: number, x: number, y: number): boolean {
  const bits = treeBits[layer];
  if (!bits) return true;
  const at = ((1 << (2 * z)) - 1) / 3 + y * (1 << z) + x;
  return ((bits[at >> 3]! >> (at & 7)) & 1) === 1;
}

const LIGHT_SUFFIX: Record<string, string> = { nrm: ".nrm.webp", hz: ".hz.webp" };

/** One tile's file: `kind` a light kind or "" for the colour tile, `dense` the @2x tree.
 *  "" for a tree tile the sparse trees tree does not have. */
export function siteTileUrl(layer: string, z: number, x: number, y: number, kind: string, dense: boolean): string {
  const row = site.layers[layer];
  if (!row) return "";
  const name = z + "/" + x + "_" + y;
  if (kind in LIGHT_SUFFIX) return row.light + "/" + name + LIGHT_SUFFIX[kind];
  if (kind === "trees" && !hasTrees(layer, z, x, y)) return "";
  if (kind) return row.dir + "/" + kind + "/" + name + row.suffix[kind];
  return row.dir + "/" + (dense ? "tiles@2x" : "tiles") + "/" + name + ".png";
}
