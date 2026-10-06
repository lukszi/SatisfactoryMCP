/* A base layer drawn unlit, lit live by the sun on one WebGL canvas over the map.
 *
 * Per tile it fetches three images of one square: the unlit colour (`?kind=unlit`), the
 * normals with sky view and land weight (`?kind=nrm`) and the horizons (`?kind=hz`: the
 * ground's, then the tree crowns', which only a style that draws the crowns reads), and the
 * shader multiplies the colour by the light. One canvas for the whole view, not one context
 * per tile. The arithmetic is mapgen's lighting/model.py; docs/spatial-and-map.md §29. */

import { tilePath } from "../api/client";
import { L } from "./leaflet";
import { MAP_SHEET_PX, map } from "./map";
import { currentSun, onSun } from "./sun";

import type { Sun } from "./sun";

export interface LightParams {
  space: string;
  ambient: number;
  sky: number[];
  sun: number[];
  tone_knee: number;
  tone_white: number;
  /** Whether this style draws the tree crowns, and so reads their horizons. */
  crowns?: boolean;
}

/** The `X-Map-Light` header of a lit layer's z0 probe. */
export interface LightHeader {
  build: string;
  max_z: number;
  unlit_max_z: number;
  params: LightParams;
  baked_sun: number[];
  model: {
    dirs: number;
    normalise_min_el: number;
    shadow_soft_deg: number;
    shadow_floor: number;
    shadow_floor_knee: number;
    /* Absent on a pyramid baked before the crowns had cells of their own. */
    shadow_fill?: number;
    hz_cells?: number;
    crown_cell?: number;
  };
}

export function parseLight(raw: string | null): LightHeader | null {
  if (!raw) return null;
  try {
    var head = JSON.parse(raw) as LightHeader;
    if (head && head.params && head.model && isFinite(head.max_z) && isFinite(head.unlit_max_z)) return head;
  } catch (ignored) {
    /* a header this page cannot read is a layer drawn the baked way */
  }
  return null;
}

var probed: boolean | null = null;

/** Whether this browser can draw a lit layer at all. */
export function webglReady(): boolean {
  if (probed === null) {
    try {
      probed = !!document.createElement("canvas").getContext("webgl2");
    } catch (ignored) {
      probed = false;
    }
  }
  return probed;
}

var VS = `#version 300 es
in vec2 aP; uniform vec4 uRect; uniform vec2 uVP; out vec2 vUV;
void main(){ vUV=aP; vec2 p=(uRect.xy+aP*uRect.zw)/uVP*2.0-1.0; gl_Position=vec4(p.x,-p.y,0.,1.); }`;

var FS = `#version 300 es
precision highp float;
in vec2 vUV; out vec4 o;
uniform sampler2D tCol, tNrm, tHz;
uniform vec3 uL, uSky, uSun, uF;
uniform float uEl, uInvNorm, uAmb, uTK, uTW, uSoft, uShadowOn, uSkyOn, uW, uFloor, uKnee, uLinear;
uniform float uFill, uRows, uCrownOn;
uniform int uI0, uI1, uCrown;
float s2l(float c){ return c<=0.04045? c/12.92 : pow((c+0.055)/1.055,2.4); }
float l2s(float c){ c=clamp(c,0.0,1.0); return c<=0.0031308? c*12.92 : 1.055*pow(c,1.0/2.4)-0.055; }
float hz(int i){
  vec2 cell=vec2(float(i%8), float(i/8));
  vec2 uv=clamp(vUV, vec2(0.5/128.0), vec2(1.0-0.5/128.0));
  float q=texture(tHz,(cell+uv)*vec2(0.125,1.0/uRows)).r;
  return q*q*90.0;
}
float horizon(){
  float h=mix(hz(uI0),hz(uI1),uW);
  if(uCrownOn>0.5) h=max(h,mix(hz(uI0+uCrown),hz(uI1+uCrown),uW));
  return h;
}
const vec3 LUMA=vec3(0.2126,0.7152,0.0722);
float tone1(float y){ if(uTK>=1.0||y<=uTK) return y; float sp=1.0-uTK; float x=(y-uTK)/sp; float t=(uTW-uTK)/sp; return uTK+sp*x*(1.0+x/(t*t))/(1.0+x); }
float untone1(float y){ if(uTK>=1.0||y<=uTK) return y; float sp=1.0-uTK; float u=min((y-uTK)/sp,0.999); float t=(uTW-uTK)/sp; float a=1.0/(t*t); float b=1.0-u; return uTK+sp*(sqrt(b*b+4.0*a*u)-b)/(2.0*a); }
vec3 tone(vec3 x){ float y=max(dot(x,LUMA),1e-7); return x*(tone1(y)/y); }
vec3 untone(vec3 x){ float y=max(dot(x,LUMA),1e-7); return x*(untone1(y)/y); }
void main(){
  vec3 c=texture(tCol,vUV).rgb; vec4 n4=texture(tNrm,vUV);
  vec3 base= uLinear>0.5 ? untone(vec3(s2l(c.r),s2l(c.g),s2l(c.b))) : c;
  vec2 nxy=n4.rg*2.0-1.0; vec3 nn=vec3(nxy, sqrt(max(1.0-dot(nxy,nxy),0.0)));
  float ndl=max(dot(nn,uL),0.0);
  float sh=uShadowOn*clamp((horizon()-uEl)/uSoft+0.5,0.0,1.0);
  float svf=mix(1.0,n4.b,uSkyOn);
  vec3 rel=(uAmb*uSky*svf+(1.0-uAmb)*uSun*ndl*(1.0-sh*(1.0-uFill))*uInvNorm)/uF;
  rel=0.5*(rel+uFloor+sqrt((rel-uFloor)*(rel-uFloor)+uKnee*uKnee));
  vec3 x=base*mix(vec3(1.0),rel,n4.a);
  vec3 t=tone(x);
  o = uLinear>0.5 ? vec4(l2s(t.r),l2s(t.g),l2s(t.b),1.0) : vec4(clamp(x,0.0,1.0),1.0);
}`;

var UNIFORMS = ["uRect", "uVP", "tCol", "tNrm", "tHz", "uL", "uSky", "uSun", "uF", "uEl", "uInvNorm", "uAmb",
  "uTK", "uTW", "uSoft", "uShadowOn", "uSkyOn", "uW", "uFloor", "uKnee", "uLinear", "uI0", "uI1", "uFill", "uRows",
  "uCrownOn", "uCrown"];
var KINDS = ["unlit", "nrm", "hz"];
var CACHE_TILES = 120;
var IN_FLIGHT = 8;
var FAILS_TO_GIVE_UP = 6;
var COARSE_LEVELS = 4;

/** One tile's three textures, and the frame that last drew it. */
interface CachedTile {
  tex: WebGLTexture[];
  used: number;
}

type Uniforms = Record<string, WebGLUniformLocation | null>;

/** The sheet's two corners in container pixels: everything else is linear between them. */
interface SheetOnScreen {
  x0: number;
  y0: number;
  span: number;
}

function normaliseToLuma(rgb: number[]): number[] {
  const luma = 0.2126 * rgb[0]! + 0.7152 * rgb[1]! + 0.0722 * rgb[2]!;
  return rgb.map(function (channel) {
    return channel / luma;
  });
}

function compile(gl: WebGL2RenderingContext): WebGLProgram {
  const program = gl.createProgram()!;
  [[gl.VERTEX_SHADER, VS] as const, [gl.FRAGMENT_SHADER, FS] as const].forEach(function (pair) {
    const shader = gl.createShader(pair[0])!;
    gl.shaderSource(shader, pair[1]);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || "shader");
    gl.attachShader(program, shader);
  });
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || "link");
  return program;
}

function texture(gl: WebGL2RenderingContext, bitmap: ImageBitmap, grey: boolean): WebGLTexture {
  const made = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, made);
  if (grey) gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, gl.RED, gl.UNSIGNED_BYTE, bitmap);
  else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return made;
}

function fetchBitmap(address: string): Promise<ImageBitmap> {
  return fetch(address).then(function (response) {
    if (!response.ok) throw new Error(address + ": " + response.status);
    return response.blob().then(function (blob) {
      return createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" });
    });
  });
}

/** The tiles held as textures, the ones in the air, and the ones still to ask for. */
interface TileTextureCache {
  /** Start a frame: these tiles are wanted, coarse first; load what is missing. */
  request(keys: string[]): void;
  /** A loaded tile, marked as drawn this frame, or undefined while it is not here yet. */
  use(key: string): CachedTile | undefined;
  /** Drop the longest-unused tiles past the budget, sparing `keep`. */
  evict(keep: Set<string>): void;
  /** Delete every texture and stop loading; replies still in the air are dropped. */
  dispose(): void;
}

/* Tiles are fetched IN_FLIGHT at a time. `onGiveUp` fires when too many fail, or the root does:
 * the layer is then swapped for its baked twin. */
function createTileTextureCache(
  gl: WebGL2RenderingContext,
  urlFor: (kind: string, z: number, x: number, y: number) => string,
  onLoaded: () => void,
  onGiveUp: (why: string) => void
): TileTextureCache {
  const cache = new Map<string, CachedTile>();
  const pending = new Set<string>();
  let queue: string[] = [];
  let fails = 0;
  let tick = 0;
  let disposed = false;

  function load(key: string): void {
    const parts = key.split("/").map(Number);
    pending.add(key);
    Promise.all(
      KINDS.map(function (kind) {
        return fetchBitmap(urlFor(kind, parts[0]!, parts[1]!, parts[2]!));
      })
    )
      .then(function (bitmaps) {
        pending.delete(key);
        if (disposed) return;
        cache.set(key, {
          tex: bitmaps.map(function (bitmap, i) {
            return texture(gl, bitmap, i === 2);
          }),
          used: tick,
        });
        bitmaps.forEach(function (bitmap) {
          bitmap.close();
        });
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
      if (!cache.has(key) && !pending.has(key)) load(key);
    }
  }

  return {
    request: function (keys) {
      tick += 1;
      queue = keys.filter(function (key) {
        return !cache.has(key) && !pending.has(key);
      });
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
        row[1].tex.forEach(function (tex) {
          gl.deleteTexture(tex);
        });
        cache.delete(row[0]);
      });
    },
    dispose: function () {
      disposed = true;
      cache.forEach(function (held) {
        held.tex.forEach(function (tex) {
          gl.deleteTexture(tex);
        });
      });
      cache.clear();
      queue = [];
    },
  };
}

/* The uniforms that hold for the layer's whole life: the light's colours and tone curve, and
 * the shadow model the pyramid was baked with. */
function uploadLightUniforms(gl: WebGL2RenderingContext, uniforms: Uniforms, light: LightHeader): void {
  const params = light.params;
  const sky = normaliseToLuma(params.sky);
  const sun = normaliseToLuma(params.sun);
  gl.uniform3f(uniforms.uSky!, sky[0]!, sky[1]!, sky[2]!);
  gl.uniform3f(uniforms.uSun!, sun[0]!, sun[1]!, sun[2]!);
  gl.uniform3f(uniforms.uF!, params.ambient * sky[0]! + (1 - params.ambient) * sun[0]!,
    params.ambient * sky[1]! + (1 - params.ambient) * sun[1]!, params.ambient * sky[2]! + (1 - params.ambient) * sun[2]!);
  gl.uniform1f(uniforms.uAmb!, params.ambient);
  gl.uniform1f(uniforms.uTK!, params.tone_knee);
  gl.uniform1f(uniforms.uTW!, params.tone_white);
  gl.uniform1f(uniforms.uLinear!, params.space === "linear" ? 1 : 0);
  const model = light.model;
  gl.uniform1f(uniforms.uSoft!, model.shadow_soft_deg);
  gl.uniform1f(uniforms.uFill!, model.shadow_fill || 0);
  gl.uniform1f(uniforms.uFloor!, model.shadow_floor);
  gl.uniform1f(uniforms.uKnee!, model.shadow_floor_knee);
  gl.uniform1f(uniforms.uRows!, Math.ceil((model.hz_cells || model.dirs) / 8));
  gl.uniform1i(uniforms.uCrown!, model.crown_cell || 0);
  gl.uniform1f(uniforms.uCrownOn!, params.crowns && model.crown_cell ? 1 : 0);
}

/* The uniforms that follow the sun: its direction, the normalisation, and which two of the
 * baked horizon directions to blend between. */
function uploadSunUniforms(gl: WebGL2RenderingContext, uniforms: Uniforms, light: LightHeader, sun: Sun): void {
  const model = light.model;
  const toRadians = Math.PI / 180;
  const azimuth = sun.az * toRadians;
  const elevation = sun.el * toRadians;
  gl.uniform3f(uniforms.uL!, Math.cos(elevation) * Math.sin(azimuth), -Math.cos(elevation) * Math.cos(azimuth), Math.sin(elevation));
  gl.uniform1f(uniforms.uEl!, sun.el);
  gl.uniform1f(uniforms.uInvNorm!, 1 / Math.max(Math.sin(elevation), Math.sin(model.normalise_min_el * toRadians)));
  const dirs = model.dirs;
  const direction = sun.az / (360 / dirs);
  const lower = Math.floor(direction) % dirs;
  gl.uniform1i(uniforms.uI0!, lower);
  gl.uniform1i(uniforms.uI1!, (lower + 1) % dirs);
  gl.uniform1f(uniforms.uW!, direction - Math.floor(direction));
  gl.uniform1f(uniforms.uShadowOn!, sun.shadows ? 1 : 0);
  gl.uniform1f(uniforms.uSkyOn!, sun.sky ? 1 : 0);
}

/** The sheet's two corners in container pixels, now. */
function sheetOnScreen(): SheetOnScreen {
  const nw = map.latLngToContainerPoint(map.unproject(L.point(0, 0), 0));
  const se = map.latLngToContainerPoint(map.unproject(L.point(MAP_SHEET_PX, MAP_SHEET_PX), 0));
  return { x0: nw.x, y0: nw.y, span: se.x - nw.x };
}

/** Every tile of one pyramid level that overlaps the view, as "z/x/y" keys. */
function visibleTiles(tileZoom: number, sheet: SheetOnScreen): string[] {
  const size = map.getSize();
  const count = 1 << tileZoom;
  const cell = sheet.span / count;
  const x0 = Math.max(0, Math.floor(-sheet.x0 / cell));
  const x1 = Math.min(count - 1, Math.floor((size.x - sheet.x0) / cell));
  const y0 = Math.max(0, Math.floor(-sheet.y0 / cell));
  const y1 = Math.min(count - 1, Math.floor((size.y - sheet.y0) / cell));
  const keys: string[] = [];
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) keys.push(tileZoom + "/" + x + "/" + y);
  return keys;
}

/* Draw the loaded tiles from COARSE_LEVELS below `tileZoom` up to it, finest last, so a missing
 * tile shows its coarser parent. Returns the keys it drew. */
function drawVisibleTiles(
  gl: WebGL2RenderingContext,
  uniforms: Uniforms,
  cache: TileTextureCache,
  sheet: SheetOnScreen,
  tileZoom: number,
  dpr: number
): string[] {
  const drawn: string[] = [];
  for (let zoom = Math.max(0, tileZoom - COARSE_LEVELS); zoom <= tileZoom; zoom++) {
    const cell = (sheet.span / (1 << zoom)) * dpr;
    visibleTiles(zoom, sheet).forEach(function (key) {
      const held = cache.use(key);
      if (!held) return;
      drawn.push(key);
      const parts = key.split("/").map(Number);
      gl.uniform4f(uniforms.uRect!, sheet.x0 * dpr + parts[1]! * cell, sheet.y0 * dpr + parts[2]! * cell, cell, cell);
      held.tex.forEach(function (tex, i) {
        gl.activeTexture(gl.TEXTURE0 + i);
        gl.bindTexture(gl.TEXTURE_2D, tex);
      });
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    });
  }
  return drawn;
}

/** One lit layer for `tileLayerId`, or throws when WebGL will not start. `onFail` swaps it out. */
export function makeLitLayer(tileLayerId: string, light: LightHeader, onFail: (why: string) => void): L.Layer {
  const sheetZoomOffset = Math.log2(MAP_SHEET_PX / 256);
  const maxTileZoom = Math.min(light.max_z, light.unlit_max_z);
  let frame = 0;
  let canvas: HTMLCanvasElement | null = null;
  let gl: WebGL2RenderingContext | null = null;
  let cache: TileTextureCache | null = null;
  const uniforms: Uniforms = {};
  let drawnZoom = 0;
  let drawnCorner: L.LatLng | null = null;
  const self = new L.Layer();

  function url(kind: string, z: number, x: number, y: number): string {
    return tilePath(tileLayerId, z, x, y) + "?kind=" + kind + "&v=" + encodeURIComponent(light.build);
  }

  function tileZoomForView(): number {
    const tileZoom = Math.round(map.getZoom() + sheetZoomOffset + Math.log2(window.devicePixelRatio || 1));
    return Math.max(0, Math.min(maxTileZoom, tileZoom));
  }

  function draw(): void {
    frame = 0;
    if (!gl || !canvas || !cache) return;
    const size = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.round(size.x * dpr);
    const height = Math.round(size.y * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    canvas.style.width = size.x + "px";
    canvas.style.height = size.y + "px";
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    drawnZoom = map.getZoom();
    drawnCorner = map.containerPointToLatLng([0, 0]);
    const sheet = sheetOnScreen();
    const tileZoom = tileZoomForView();
    const want = visibleTiles(tileZoom, sheet);
    const coarse = visibleTiles(Math.max(0, tileZoom - COARSE_LEVELS), sheet);
    cache.request(coarse.concat(want));
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(uniforms.uVP!, width, height);
    uploadSunUniforms(gl, uniforms, light, currentSun());
    const keep = new Set<string>(want.concat(coarse));
    drawVisibleTiles(gl, uniforms, cache, sheet, tileZoom, dpr).forEach(function (key) {
      keep.add(key);
    });
    cache.evict(keep);
  }

  function redraw(): void {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function animZoom(event: L.ZoomAnimEvent): void {
    if (!canvas || !drawnCorner) return;
    const scale = map.getZoomScale(event.zoom, drawnZoom);
    const private_ = map as unknown as {
      _latLngToNewLayerPoint(at: L.LatLng, zoom: number, centre: L.LatLng): L.Point;
    };
    L.DomUtil.setTransform(canvas, private_._latLngToNewLayerPoint(drawnCorner, event.zoom, event.center), scale);
  }

  function start(): void {
    canvas = L.DomUtil.create("canvas", "lit-layer leaflet-zoom-animated");
    const made = canvas.getContext("webgl2", { antialias: false, premultipliedAlpha: false });
    if (!made) throw new Error("no WebGL2");
    gl = made;
    const program = compile(made);
    made.useProgram(program);
    UNIFORMS.forEach(function (name) {
      uniforms[name] = made.getUniformLocation(program, name);
    });
    const buffer = made.createBuffer();
    made.bindBuffer(made.ARRAY_BUFFER, buffer);
    made.bufferData(made.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), made.STATIC_DRAW);
    const corner = made.getAttribLocation(program, "aP");
    made.enableVertexAttribArray(corner);
    made.vertexAttribPointer(corner, 2, made.FLOAT, false, 0, 0);
    made.uniform1i(uniforms.tCol!, 0);
    made.uniform1i(uniforms.tNrm!, 1);
    made.uniform1i(uniforms.tHz!, 2);
    made.pixelStorei(made.UNPACK_COLORSPACE_CONVERSION_WEBGL, made.NONE);
    made.pixelStorei(made.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    uploadLightUniforms(made, uniforms, light);
    cache = createTileTextureCache(made, url, redraw, onFail);
    canvas.addEventListener("webglcontextlost", function (event) {
      event.preventDefault();
      if (gl) onFail("the graphics context was lost");
    });
  }

  const events: Record<string, L.LeafletEventHandlerFn> = {
    move: redraw,
    moveend: redraw,
    zoomend: redraw,
    viewreset: redraw,
    resize: redraw,
    zoomanim: animZoom as L.LeafletEventHandlerFn,
  };

  start();
  self.onAdd = function (m: L.Map) {
    m.getPane("basemap")!.appendChild(canvas!);
    m.on(events);
    draw();
    return self;
  };
  self.onRemove = function (m: L.Map) {
    m.off(events);
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (cache) cache.dispose();
    const lostGl = gl;
    gl = null;
    const lose = lostGl ? lostGl.getExtension("WEBGL_lose_context") : null;
    if (lose) lose.loseContext();
    if (canvas) L.DomUtil.remove(canvas);
    canvas = null;
    return self;
  };
  onSun(function () {
    if (gl) redraw();
  });
  return self;
}
