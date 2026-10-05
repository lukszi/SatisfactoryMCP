/* A base layer drawn unlit, lit live by the sun on one WebGL canvas over the map.
 *
 * Per tile it fetches three images of one square: the unlit colour (`?kind=unlit`), the
 * normals with sky view and land weight (`?kind=nrm`) and the horizons (`?kind=hz`: the
 * ground's, then the tree crowns', which only a style that draws the crowns reads), and the
 * shader multiplies the colour by the light. One canvas for the whole view, not one context
 * per tile. The arithmetic is mapgen's lighting/model.py; docs/spatial-and-map.md §29. */

import { tilePath } from "./api";
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

interface Held {
  tex: WebGLTexture[];
  used: number;
}

function unit(rgb: number[]): number[] {
  var y = 0.2126 * rgb[0]! + 0.7152 * rgb[1]! + 0.0722 * rgb[2]!;
  return rgb.map(function (v) {
    return v / y;
  });
}

function compile(gl: WebGL2RenderingContext): WebGLProgram {
  var program = gl.createProgram()!;
  [[gl.VERTEX_SHADER, VS] as const, [gl.FRAGMENT_SHADER, FS] as const].forEach(function (pair) {
    var shader = gl.createShader(pair[0])!;
    gl.shaderSource(shader, pair[1]);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || "shader");
    gl.attachShader(program, shader);
  });
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || "link");
  return program;
}

/** One lit layer for `layer`, or throws when WebGL will not start. `onFail` swaps it out. */
export function makeLitLayer(layer: string, light: LightHeader, onFail: (why: string) => void): L.Layer {
  var top = Math.log2(MAP_SHEET_PX / 256);
  var maxT = Math.min(light.max_z, light.unlit_max_z);
  var cache = new Map<string, Held>();
  var pending = new Set<string>();
  var queue: string[] = [];
  var fails = 0;
  var tick = 0;
  var frame = 0;
  var canvas: HTMLCanvasElement | null = null;
  var gl: WebGL2RenderingContext | null = null;
  var U: Record<string, WebGLUniformLocation | null> = {};
  var drawnZoom = 0;
  var drawnCorner: L.LatLng | null = null;
  var self = new L.Layer();

  function url(kind: string, z: number, x: number, y: number): string {
    return tilePath(layer, z, x, y) + "?kind=" + kind + "&v=" + encodeURIComponent(light.build);
  }

  function texture(bitmap: ImageBitmap, grey: boolean): WebGLTexture {
    var g = gl!;
    var t = g.createTexture()!;
    g.bindTexture(g.TEXTURE_2D, t);
    if (grey) g.texImage2D(g.TEXTURE_2D, 0, g.R8, g.RED, g.UNSIGNED_BYTE, bitmap);
    else g.texImage2D(g.TEXTURE_2D, 0, g.RGBA8, g.RGBA, g.UNSIGNED_BYTE, bitmap);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MIN_FILTER, g.LINEAR);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_MAG_FILTER, g.LINEAR);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_S, g.CLAMP_TO_EDGE);
    g.texParameteri(g.TEXTURE_2D, g.TEXTURE_WRAP_T, g.CLAMP_TO_EDGE);
    return t;
  }

  function fetchBitmap(address: string): Promise<ImageBitmap> {
    return fetch(address).then(function (r) {
      if (!r.ok) throw new Error(address + ": " + r.status);
      return r.blob().then(function (blob) {
        return createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" });
      });
    });
  }

  function load(key: string): void {
    var parts = key.split("/").map(Number);
    pending.add(key);
    Promise.all(
      KINDS.map(function (kind) {
        return fetchBitmap(url(kind, parts[0]!, parts[1]!, parts[2]!));
      })
    )
      .then(function (bitmaps) {
        pending.delete(key);
        if (!gl) return;
        cache.set(key, {
          tex: bitmaps.map(function (b, i) {
            return texture(b, i === 2);
          }),
          used: tick,
        });
        bitmaps.forEach(function (b) {
          b.close();
        });
        redraw();
        pump();
      })
      .catch(function () {
        pending.delete(key);
        fails += 1;
        if (fails >= FAILS_TO_GIVE_UP || key === "0/0/0") onFail("the lighting tiles would not load");
        pump();
      });
  }

  function pump(): void {
    while (pending.size < IN_FLIGHT && queue.length) {
      var key = queue.shift()!;
      if (!cache.has(key) && !pending.has(key)) load(key);
    }
  }

  function level(): number {
    var t = Math.round(map.getZoom() + top + Math.log2(window.devicePixelRatio || 1));
    return Math.max(0, Math.min(maxT, t));
  }

  /* Container pixels of the sheet's two corners: everything else is linear between them. */
  function frameOf(): { x0: number; y0: number; span: number } {
    var nw = map.latLngToContainerPoint(map.unproject(L.point(0, 0), 0));
    var se = map.latLngToContainerPoint(map.unproject(L.point(MAP_SHEET_PX, MAP_SHEET_PX), 0));
    return { x0: nw.x, y0: nw.y, span: se.x - nw.x };
  }

  function visible(t: number, f: { x0: number; y0: number; span: number }): string[] {
    var size = map.getSize();
    var n = 1 << t;
    var cell = f.span / n;
    var x0 = Math.max(0, Math.floor(-f.x0 / cell));
    var x1 = Math.min(n - 1, Math.floor((size.x - f.x0) / cell));
    var y0 = Math.max(0, Math.floor(-f.y0 / cell));
    var y1 = Math.min(n - 1, Math.floor((size.y - f.y0) / cell));
    var out: string[] = [];
    for (var y = y0; y <= y1; y++) for (var x = x0; x <= x1; x++) out.push(t + "/" + x + "/" + y);
    return out;
  }

  function evict(keep: Set<string>): void {
    if (cache.size <= CACHE_TILES) return;
    var rows = Array.from(cache.entries()).filter(function (row) {
      return !keep.has(row[0]);
    });
    rows.sort(function (a, b) {
      return a[1].used - b[1].used;
    });
    rows.slice(0, cache.size - CACHE_TILES).forEach(function (row) {
      row[1].tex.forEach(function (t) {
        gl!.deleteTexture(t);
      });
      cache.delete(row[0]);
    });
  }

  function setSun(g: WebGL2RenderingContext, sun: Sun): void {
    var d = Math.PI / 180;
    var az = sun.az * d;
    var el = sun.el * d;
    g.uniform3f(U.uL!, Math.cos(el) * Math.sin(az), -Math.cos(el) * Math.cos(az), Math.sin(el));
    g.uniform1f(U.uEl!, sun.el);
    g.uniform1f(U.uInvNorm!, 1 / Math.max(Math.sin(el), Math.sin(light.model.normalise_min_el * d)));
    var dirs = light.model.dirs;
    var f = sun.az / (360 / dirs);
    var i0 = Math.floor(f) % dirs;
    g.uniform1i(U.uI0!, i0);
    g.uniform1i(U.uI1!, (i0 + 1) % dirs);
    g.uniform1f(U.uW!, f - Math.floor(f));
    g.uniform1f(U.uShadowOn!, sun.shadows ? 1 : 0);
    g.uniform1f(U.uSkyOn!, sun.sky ? 1 : 0);
  }

  function draw(): void {
    frame = 0;
    if (!gl || !canvas) return;
    var g = gl;
    var size = map.getSize();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.round(size.x * dpr);
    var h = Math.round(size.y * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    canvas.style.width = size.x + "px";
    canvas.style.height = size.y + "px";
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    drawnZoom = map.getZoom();
    drawnCorner = map.containerPointToLatLng([0, 0]);
    var f = frameOf();
    var t = level();
    var want = visible(t, f);
    var coarse = visible(Math.max(0, t - COARSE_LEVELS), f);
    tick += 1;
    queue = coarse.concat(want).filter(function (key) {
      return !cache.has(key) && !pending.has(key);
    });
    pump();
    g.viewport(0, 0, w, h);
    g.clearColor(0, 0, 0, 0);
    g.clear(g.COLOR_BUFFER_BIT);
    g.uniform2f(U.uVP!, w, h);
    setSun(g, currentSun());
    var keep = new Set<string>(want.concat(coarse));
    for (var level0 = Math.max(0, t - COARSE_LEVELS); level0 <= t; level0++) {
      var cell = (f.span / (1 << level0)) * dpr;
      visible(level0, f).forEach(function (key) {
        var held = cache.get(key);
        if (!held) return;
        held.used = tick;
        keep.add(key);
        var p = key.split("/").map(Number);
        g.uniform4f(U.uRect!, f.x0 * dpr + p[1]! * cell, f.y0 * dpr + p[2]! * cell, cell, cell);
        held.tex.forEach(function (tex, i) {
          g.activeTexture(g.TEXTURE0 + i);
          g.bindTexture(g.TEXTURE_2D, tex);
        });
        g.drawArrays(g.TRIANGLE_STRIP, 0, 4);
      });
    }
    evict(keep);
  }

  function redraw(): void {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function animZoom(event: L.ZoomAnimEvent): void {
    if (!canvas || !drawnCorner) return;
    var scale = map.getZoomScale(event.zoom, drawnZoom);
    var private_ = map as unknown as {
      _latLngToNewLayerPoint(at: L.LatLng, zoom: number, centre: L.LatLng): L.Point;
    };
    L.DomUtil.setTransform(canvas, private_._latLngToNewLayerPoint(drawnCorner, event.zoom, event.center), scale);
  }

  function start(): void {
    canvas = L.DomUtil.create("canvas", "lit-layer leaflet-zoom-animated");
    var made = canvas.getContext("webgl2", { antialias: false, premultipliedAlpha: false });
    if (!made) throw new Error("no WebGL2");
    var g: WebGL2RenderingContext = made;
    gl = g;
    var program = compile(g);
    g.useProgram(program);
    UNIFORMS.forEach(function (name) {
      U[name] = g.getUniformLocation(program, name);
    });
    var buffer = g.createBuffer();
    g.bindBuffer(g.ARRAY_BUFFER, buffer);
    g.bufferData(g.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), g.STATIC_DRAW);
    var at = g.getAttribLocation(program, "aP");
    g.enableVertexAttribArray(at);
    g.vertexAttribPointer(at, 2, g.FLOAT, false, 0, 0);
    g.uniform1i(U.tCol!, 0);
    g.uniform1i(U.tNrm!, 1);
    g.uniform1i(U.tHz!, 2);
    g.pixelStorei(g.UNPACK_COLORSPACE_CONVERSION_WEBGL, g.NONE);
    g.pixelStorei(g.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    var p = light.params;
    var sky = unit(p.sky);
    var sun = unit(p.sun);
    g.uniform3f(U.uSky!, sky[0]!, sky[1]!, sky[2]!);
    g.uniform3f(U.uSun!, sun[0]!, sun[1]!, sun[2]!);
    g.uniform3f(U.uF!, p.ambient * sky[0]! + (1 - p.ambient) * sun[0]!, p.ambient * sky[1]! + (1 - p.ambient) * sun[1]!,
      p.ambient * sky[2]! + (1 - p.ambient) * sun[2]!);
    g.uniform1f(U.uAmb!, p.ambient);
    g.uniform1f(U.uTK!, p.tone_knee);
    g.uniform1f(U.uTW!, p.tone_white);
    g.uniform1f(U.uLinear!, p.space === "linear" ? 1 : 0);
    var m = light.model;
    g.uniform1f(U.uSoft!, m.shadow_soft_deg);
    g.uniform1f(U.uFill!, m.shadow_fill || 0);
    g.uniform1f(U.uFloor!, m.shadow_floor);
    g.uniform1f(U.uKnee!, m.shadow_floor_knee);
    g.uniform1f(U.uRows!, Math.ceil((m.hz_cells || m.dirs) / 8));
    g.uniform1i(U.uCrown!, m.crown_cell || 0);
    g.uniform1f(U.uCrownOn!, p.crowns && m.crown_cell ? 1 : 0);
    canvas.addEventListener("webglcontextlost", function (event) {
      event.preventDefault();
      if (gl) onFail("the graphics context was lost");
    });
  }

  var events: Record<string, L.LeafletEventHandlerFn> = {
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
    cache.forEach(function (held) {
      held.tex.forEach(function (t) {
        gl!.deleteTexture(t);
      });
    });
    cache.clear();
    queue = [];
    var held = gl;
    gl = null;
    var lose = held ? held.getExtension("WEBGL_lose_context") : null;
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
