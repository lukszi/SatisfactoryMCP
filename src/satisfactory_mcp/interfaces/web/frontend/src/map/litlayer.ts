/* A base layer drawn unlit, lit live by the sun on one WebGL canvas over the map.
 *
 * Per tile it fetches the unlit colour (`?kind=unlit`), the normals with sky view and land
 * weight (`?kind=nrm`) and the horizons (`?kind=hz`: the ground's, then the trees', which only
 * a style that draws the trees reads); on a layer drawn apart at its trees, the colour is the
 * ground and the trees come as a fourth image (`?kind=trees`) while they are shown. The shader
 * lights both and lays the trees over the ground. One canvas for the whole view, not one
 * context per tile. The arithmetic is mapgen's lighting/model.py; docs/spatial-and-map.md §29.
 * The switches (shade, trees, terrain and tree shadows, sky) are uniforms: `lightSwitches`. */

import { tilePath } from "../api/client";
import { L } from "./leaflet";
import { createTileTextureCache, noTrees } from "./littiles";
import { MAP_SHEET_PX, map } from "./map";
import { currentSun, onSun } from "./sun";

import type { TileTextureCache } from "./littiles";
import type { LightControls } from "./suncontrol";
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
    /* Absent on a pyramid baked before each cell had a border of its edge texels. */
    hz_gutter?: number;
    /* Absent before the Titan trees had cells of their own and the trees' sky occlusion one. */
    titan_cell?: number;
    ao_cell?: number;
  };
  /** What the layer is made of besides the light; absent or empty on a layer drawn whole. */
  parts?: { trees?: { max_z: number; sparse: boolean } };
}

export function parseLight(raw: string | null): LightHeader | null {
  if (!raw) return null;
  try {
    const head = JSON.parse(raw) as LightHeader;
    if (head?.params && head.model && isFinite(head.max_z) && isFinite(head.unlit_max_z)) return head;
  } catch (ignored) {
    /* a header this page cannot read is a layer drawn the baked way */
  }
  return null;
}

let probed: boolean | null = null;

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

const VS = `#version 300 es
in vec2 aP; uniform vec4 uRect; uniform vec2 uVP; out vec2 vUV;
void main(){ vUV=aP; vec2 p=(uRect.xy+aP*uRect.zw)/uVP*2.0-1.0; gl_Position=vec4(p.x,-p.y,0.,1.); }`;

export const FS = `#version 300 es
precision highp float;
in vec2 vUV; out vec4 o;
uniform sampler2D tCol, tNrm, tHz, tTrees;
uniform vec3 uL, uSky, uSun, uF;
uniform float uEl, uInvNorm, uAmb, uTK, uTW, uSoft, uGroundSh, uSkyOn, uW, uFloor, uKnee, uLinear;
uniform float uFill, uRows, uCrownSh, uLightOn, uGut, uIn, uTreesOn;
uniform int uI0, uI1, uCrown, uTitan, uAo;
float s2l(float c){ return c<=0.04045? c/12.92 : pow((c+0.055)/1.055,2.4); }
float l2s(float c){ c=clamp(c,0.0,1.0); return c<=0.0031308? c*12.92 : 1.055*pow(c,1.0/2.4)-0.055; }
float raw(int i){
  vec2 cell=vec2(float(i%8), float(i/8));
  vec2 uv=clamp(vUV, vec2(0.5/128.0), vec2(1.0-0.5/128.0));
  return texture(tHz,(cell+uGut+uv*uIn)*vec2(0.125,1.0/uRows)).r;
}
float hz(int i){ float q=raw(i); return q*q*90.0; }
float horizon(){
  float h=uGroundSh*mix(hz(uI0),hz(uI1),uW);
  if(uCrownSh*uTreesOn>0.5){
    h=max(h,mix(hz(uI0+uCrown),hz(uI1+uCrown),uW));
    if(uTitan>0) h=max(h,mix(hz(uI0+uTitan),hz(uI1+uTitan),uW));
  }
  return h;
}
const vec3 LUMA=vec3(0.2126,0.7152,0.0722);
float tone1(float y){ if(uTK>=1.0||y<=uTK) return y; float sp=1.0-uTK; float x=(y-uTK)/sp; float t=(uTW-uTK)/sp; return uTK+sp*x*(1.0+x/(t*t))/(1.0+x); }
float untone1(float y){ if(uTK>=1.0||y<=uTK) return y; float sp=1.0-uTK; float u=min((y-uTK)/sp,0.999); float t=(uTW-uTK)/sp; float a=1.0/(t*t); float b=1.0-u; return uTK+sp*(sqrt(b*b+4.0*a*u)-b)/(2.0*a); }
vec3 tone(vec3 x){ float y=max(dot(x,LUMA),1e-7); return x*(tone1(y)/y); }
vec3 untone(vec3 x){ float y=max(dot(x,LUMA),1e-7); return x*(untone1(y)/y); }
vec3 toLinear(vec3 c){ return uLinear>0.5 ? untone(vec3(s2l(c.r),s2l(c.g),s2l(c.b))) : c; }
void main(){
  vec3 base=toLinear(texture(tCol,vUV).rgb); vec4 n4=texture(tNrm,vUV);
  vec4 t4=texture(tTrees,vUV); float a=t4.a*uTreesOn;
  vec3 tree=a>0.0 ? toLinear(t4.rgb/t4.a) : vec3(0.0);
  vec2 nxy=n4.rg*2.0-1.0; vec3 nn=vec3(nxy, sqrt(max(1.0-dot(nxy,nxy),0.0)));
  float ndl=max(dot(nn,uL),0.0);
  float sh=max(uGroundSh,uCrownSh*uTreesOn)*clamp((horizon()-uEl)/uSoft+0.5,0.0,1.0);
  float ao=(uAo>0 && uTreesOn>0.5) ? raw(uAo) : 0.0;
  float svf=mix(1.0,n4.b*(1.0-ao),uSkyOn);
  vec3 rel=(uAmb*uSky*svf+(1.0-uAmb)*uSun*ndl*(1.0-sh*(1.0-uFill))*uInvNorm)/uF;
  rel=0.5*(rel+uFloor+sqrt((rel-uFloor)*(rel-uFloor)+uKnee*uKnee));
  vec3 x=mix(base*mix(vec3(1.0),rel,n4.a*uLightOn), tree*mix(vec3(1.0),rel,uLightOn), a);
  vec3 t=tone(x);
  o = uLinear>0.5 ? vec4(l2s(t.r),l2s(t.g),l2s(t.b),1.0) : vec4(clamp(x,0.0,1.0),1.0);
}`;

export const UNIFORMS = ["uRect", "uVP", "tCol", "tNrm", "tHz", "tTrees", "uL", "uSky", "uSun", "uF", "uEl", "uInvNorm",
  "uAmb", "uTK", "uTW", "uSoft", "uGroundSh", "uSkyOn", "uW", "uFloor", "uKnee", "uLinear", "uI0", "uI1", "uFill", "uRows",
  "uCrownSh", "uCrown", "uLightOn", "uGut", "uIn", "uTreesOn", "uTitan", "uAo"];
/** A horizon cell's own texels a side; the pyramid's `hz_gutter` borders it. */
const HZ_CELL_PX = 128;
const COARSE_LEVELS = 4;

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
  gl.uniform1i(uniforms.uTitan!, model.titan_cell || 0);
  gl.uniform1i(uniforms.uAo!, model.ao_cell || 0);
  const stride = HZ_CELL_PX + 2 * (model.hz_gutter || 0);
  gl.uniform1f(uniforms.uGut!, (model.hz_gutter || 0) / stride);
  gl.uniform1f(uniforms.uIn!, HZ_CELL_PX / stride);
}

/** Whether this layer draws the tree crowns and its pyramid holds their horizons. */
export function castsTreeShadows(light: LightHeader): boolean {
  return !!(light.params.crowns && light.model.crown_cell);
}

/** Whether the layer holds its trees apart from the ground, so the trees can be switched. */
export function treesApart(light: LightHeader): boolean {
  return !!(light.parts && light.parts.trees);
}

/** What the map's controls offer for this light; `off` is why it is not drawn live, or "". */
export function lightControls(light: LightHeader, off: string): LightControls {
  return { trees: castsTreeShadows(light), apart: treesApart(light), alone: !!light.model.titan_cell, off: off };
}

/** Whether the trees are drawn: always on a layer that keeps them in its colour. */
function treesShown(light: LightHeader, sun: Sun): boolean {
  return !treesApart(light) || sun.trees;
}

export type LightSwitches = Record<"uLightOn" | "uGroundSh" | "uCrownSh" | "uSkyOn" | "uTreesOn", number>;

/* The shader's switches for `sun`. The trees off take their shadows and sky occlusion with
 * them. Before the trees had cells of their own, terrain shadows off with tree shadows on loses
 * the tree shadows inside terrain shade: a crown cell was stored only where it stood higher. */
export function lightSwitches(light: LightHeader, sun: Sun): LightSwitches {
  return {
    uLightOn: sun.shade ? 1 : 0,
    uGroundSh: sun.terrainShadows ? 1 : 0,
    uCrownSh: sun.treeShadows && castsTreeShadows(light) ? 1 : 0,
    uSkyOn: sun.sky ? 1 : 0,
    uTreesOn: treesShown(light, sun) ? 1 : 0,
  };
}

/* The uniforms that follow the sun: its direction, the normalisation, which two of the baked
 * horizon directions to blend between, and the switches. */
function uploadSunUniforms(gl: WebGL2RenderingContext, uniforms: Uniforms, light: LightHeader, sun: Sun): void {
  const model = light.model;
  const toRadians = Math.PI / 180;
  const azimuth = sun.azimuthDeg * toRadians;
  const elevation = sun.elevationDeg * toRadians;
  gl.uniform3f(uniforms.uL!, Math.cos(elevation) * Math.sin(azimuth), -Math.cos(elevation) * Math.cos(azimuth), Math.sin(elevation));
  gl.uniform1f(uniforms.uEl!, sun.elevationDeg);
  gl.uniform1f(uniforms.uInvNorm!, 1 / Math.max(Math.sin(elevation), Math.sin(model.normalise_min_el * toRadians)));
  const dirs = model.dirs;
  const direction = sun.azimuthDeg / (360 / dirs);
  const lower = Math.floor(direction) % dirs;
  gl.uniform1i(uniforms.uI0!, lower);
  gl.uniform1i(uniforms.uI1!, (lower + 1) % dirs);
  gl.uniform1f(uniforms.uW!, direction - Math.floor(direction));
  const switches = lightSwitches(light, sun);
  (Object.keys(switches) as (keyof LightSwitches)[]).forEach(function (name) {
    gl.uniform1f(uniforms[name]!, switches[name]);
  });
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
  dpr: number,
  empty: WebGLTexture
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
      held.tex.concat([held.trees || empty]).forEach(function (tex, i) {
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
  const treesMaxZ = light.parts && light.parts.trees ? light.parts.trees.max_z : maxTileZoom;
  let frame = 0;
  let canvas: HTMLCanvasElement | null = null;
  let gl: WebGL2RenderingContext | null = null;
  let cache: TileTextureCache | null = null;
  let empty: WebGLTexture | null = null;
  const uniforms: Uniforms = {};
  let drawnZoom = 0;
  let drawnCorner: L.LatLng | null = null;
  const self = new L.Layer();

  function url(kind: string, z: number, x: number, y: number): string {
    return tilePath(tileLayerId, z, x, y) + "?kind=" + kind + "&v=" + encodeURIComponent(light.build);
  }

  function tileZoomForView(trees: boolean): number {
    const tileZoom = Math.round(map.getZoom() + sheetZoomOffset + Math.log2(window.devicePixelRatio || 1));
    return Math.max(0, Math.min(trees ? Math.min(maxTileZoom, treesMaxZ) : maxTileZoom, tileZoom));
  }

  function draw(): void {
    frame = 0;
    if (!gl || !canvas || !cache || !empty) return;
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
    const sun = currentSun();
    const trees = treesApart(light) && sun.trees;
    const tileZoom = tileZoomForView(trees);
    const want = visibleTiles(tileZoom, sheet);
    const coarse = visibleTiles(Math.max(0, tileZoom - COARSE_LEVELS), sheet);
    cache.request(coarse.concat(want), trees);
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(uniforms.uVP!, width, height);
    uploadSunUniforms(gl, uniforms, light, sun);
    const keep = new Set<string>(want.concat(coarse));
    drawVisibleTiles(gl, uniforms, cache, sheet, tileZoom, dpr, empty).forEach(function (key) {
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
    L.DomUtil.setTransform(canvas, map._latLngToNewLayerPoint(drawnCorner, event.zoom, event.center), scale);
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
    made.uniform1i(uniforms.tTrees!, 3);
    made.pixelStorei(made.UNPACK_COLORSPACE_CONVERSION_WEBGL, made.NONE);
    made.pixelStorei(made.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    uploadLightUniforms(made, uniforms, light);
    empty = noTrees(made);
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
    if (gl && empty) gl.deleteTexture(empty);
    empty = null;
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
