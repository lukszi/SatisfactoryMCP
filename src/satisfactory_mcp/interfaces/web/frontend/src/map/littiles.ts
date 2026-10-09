/* The textures a lit layer draws from, per tile: the unlit colour, normals and horizons, and
 * on a layer drawn apart at its trees the trees, fetched only while they are shown. A tile the
 * sparse trees tree has nothing in answers 204, and draws the shared transparent texture.
 * docs/map/light-and-crowns.md §29, "The page". */

/** The kinds every tile needs, in texture-unit order; the trees are the fourth unit. */
const KINDS = ["unlit", "nrm", "hz"];
export const TREES_KIND = "trees";
const CACHE_TILES = 120;
const IN_FLIGHT = 8;
const FAILS_TO_GIVE_UP = 6;

/** One tile's textures, and the frame that last drew it. `trees` is undefined until fetched,
 *  and null where the tile has none. */
export interface CachedTile {
  tex: WebGLTexture[];
  trees: WebGLTexture | null | undefined;
  used: number;
}

/** The tiles held as textures, the ones in the air, and the ones still to ask for. */
export interface TileTextureCache {
  /** Start a frame: these tiles are wanted, coarse first, with their trees when `trees`. */
  request(keys: string[], trees: boolean): void;
  /** A loaded tile, marked as drawn this frame, or undefined while it is not here yet. */
  use(key: string): CachedTile | undefined;
  /** Drop the longest-unused tiles past the budget, sparing `keep`. */
  evict(keep: Set<string>): void;
  /** Delete every texture and stop loading; replies still in the air are dropped. */
  dispose(): void;
}

function texture(gl: WebGL2RenderingContext, bitmap: ImageBitmap | null, grey: boolean): WebGLTexture {
  const made = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, made);
  if (!bitmap) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
  else if (grey) gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, gl.RED, gl.UNSIGNED_BYTE, bitmap);
  else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return made;
}

/** The transparent texture a tile without trees draws, one per context. */
export function noTrees(gl: WebGL2RenderingContext): WebGLTexture {
  return texture(gl, null, false);
}

/* A tile as a bitmap, or null for a 204. The trees come premultiplied, so a bilinear read
 * never pulls in the colour under alpha 0 that their lossless WebP leaves free. */
export function fetchBitmap(address: string, premultiplied = false): Promise<ImageBitmap | null> {
  return fetch(address).then(function (response) {
    if (response.status === 204) return null;
    if (!response.ok) throw new Error(address + ": " + response.status);
    return response.blob().then(function (blob) {
      return createImageBitmap(blob, {
        premultiplyAlpha: premultiplied ? "premultiply" : "none",
        colorSpaceConversion: "none",
      });
    });
  });
}

function deleteTile(gl: WebGL2RenderingContext, held: CachedTile): void {
  held.tex.concat(held.trees ? [held.trees] : []).forEach(function (tex) {
    gl.deleteTexture(tex);
  });
}

/* Tiles are fetched IN_FLIGHT at a time. `onGiveUp` fires when too many fail, or the root does:
 * the layer is then swapped for its baked twin. */
export function createTileTextureCache(
  gl: WebGL2RenderingContext,
  urlFor: (kind: string, z: number, x: number, y: number) => string,
  onLoaded: () => void,
  onGiveUp: (why: string) => void
): TileTextureCache {
  const cache = new Map<string, CachedTile>();
  const pending = new Set<string>();
  let queue: string[] = [];
  let wantTrees = false;
  let fails = 0;
  let tick = 0;
  let disposed = false;

  function missing(key: string): boolean {
    const held = cache.get(key);
    return !pending.has(key) && (!held || (wantTrees && held.trees === undefined));
  }

  function load(key: string): void {
    const parts = key.split("/").map(Number);
    const held = cache.get(key);
    const kinds = (held ? [] : KINDS).concat(wantTrees ? [TREES_KIND] : []);
    pending.add(key);
    Promise.all(
      kinds.map(function (kind) {
        return fetchBitmap(urlFor(kind, parts[0]!, parts[1]!, parts[2]!), kind === TREES_KIND);
      })
    )
      .then(function (bitmaps) {
        pending.delete(key);
        if (bitmaps.some((bitmap, i) => !bitmap && kinds[i] !== TREES_KIND)) throw new Error(key + ": a tile is missing");
        const gone = disposed || (held && cache.get(key) !== held);
        const textures = bitmaps.map(function (bitmap, i) {
          return bitmap && !gone ? texture(gl, bitmap, kinds[i] === "hz") : null;
        });
        bitmaps.forEach(function (bitmap) {
          if (bitmap) bitmap.close();
        });
        if (gone) return;
        const tile = held || { tex: textures.slice(0, KINDS.length) as WebGLTexture[], trees: undefined, used: tick };
        if (kinds[kinds.length - 1] === TREES_KIND) tile.trees = textures[textures.length - 1];
        cache.set(key, tile);
        onLoaded();
        pump();
      })
      .catch(function () {
        pending.delete(key);
        fails += 1;
        if (fails >= FAILS_TO_GIVE_UP || key === "0/0/0") onGiveUp("the lighting tiles would not load");
        pump();
      });
  }

  function pump(): void {
    while (pending.size < IN_FLIGHT && queue.length) {
      const key = queue.shift()!;
      if (missing(key)) load(key);
    }
  }

  return {
    request: function (keys, trees) {
      tick += 1;
      wantTrees = trees;
      queue = keys.filter(missing);
      pump();
    },
    use: function (key) {
      const held = cache.get(key);
      if (held) held.used = tick;
      return held;
    },
    evict: function (keep) {
      if (cache.size <= CACHE_TILES) return;
      const rows = Array.from(cache.entries()).filter(function (row) {
        return !keep.has(row[0]);
      });
      rows.sort(function (a, b) {
        return a[1].used - b[1].used;
      });
      rows.slice(0, cache.size - CACHE_TILES).forEach(function (row) {
        deleteTile(gl, row[1]);
        cache.delete(row[0]);
      });
    },
    dispose: function () {
      disposed = true;
      cache.forEach(function (held) {
        deleteTile(gl, held);
      });
      cache.clear();
      queue = [];
    },
  };
}
