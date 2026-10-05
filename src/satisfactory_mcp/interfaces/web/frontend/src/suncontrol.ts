/* The sun button: a small control on the map while a lit layer is drawn. It opens a time-of-day
 * slider on the game's sun path, four presets, the shadow and sky switches, and under
 * "advanced" a free compass. It moves the sun for this visit; Settings → map keeps the default. */

import { L } from "./leaflet";
import { map } from "./map";
import { setSetting } from "./settings";
import {
  currentSun,
  FIRST_HOUR,
  gameSun,
  hourText,
  LAST_HOUR,
  MAP_NW,
  MIN_EL,
  NOON_HOUR,
  onSun,
  placeSun,
  resetSun,
  setHour,
  sunOverridden,
} from "./sun";

import type { Sun } from "./sun";

var R = 46;
var control: L.Control | null = null;
var root: HTMLElement | null = null;
var refresh: ((sun: Sun) => void) | null = null;

function node<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text?: string): HTMLElementTagNameMap[K] {
  var made = document.createElement(tag);
  if (cls) made.className = cls;
  if (text !== undefined) made.textContent = text;
  return made;
}

var SVG = "http://www.w3.org/2000/svg";

function svg(tag: string, attrs: Record<string, string | number>): SVGElement {
  var made = document.createElementNS(SVG, tag) as SVGElement;
  Object.keys(attrs).forEach(function (k) {
    made.setAttribute(k, String(attrs[k]));
  });
  return made;
}

function polar(az: number, el: number): [number, number] {
  var r = ((90 - el) / 90) * R;
  var a = (az * Math.PI) / 180;
  return [r * Math.sin(a), -r * Math.cos(a)];
}

function compass(): { el: SVGElement; show: (sun: Sun) => void } {
  var box = svg("svg", { viewBox: "-52 -52 104 104", width: 104, height: 104, class: "sun-compass", role: "img" });
  box.setAttribute("aria-label", "sun position: drag to move the sun anywhere");
  box.appendChild(svg("circle", { r: R, class: "sun-ring" }));
  box.appendChild(svg("circle", { r: R / 2, class: "sun-ring-in" }));
  var points: string[] = [];
  for (var h = FIRST_HOUR; h <= LAST_HOUR; h += 0.1) {
    var at = gameSun(h);
    if (at[1] < 0) continue;
    var p = polar(at[0], at[1]);
    points.push(p[0].toFixed(1) + "," + p[1].toFixed(1));
  }
  box.appendChild(svg("path", { d: "M" + points.join("L"), class: "sun-path" }));
  [["N", 0, -R + 9], ["E", R - 7, 3], ["S", 0, R - 3], ["W", -R + 7, 3]].forEach(function (t) {
    var label = svg("text", { x: t[1]!, y: t[2]!, class: "sun-cardinal" });
    label.textContent = String(t[0]);
    box.appendChild(label);
  });
  var dot = svg("circle", { r: 5, class: "sun-dot" });
  box.appendChild(dot);
  function place(event: PointerEvent): void {
    var rect = box.getBoundingClientRect();
    var x = ((event.clientX - rect.left) / rect.width) * 104 - 52;
    var y = ((event.clientY - rect.top) / rect.height) * 104 - 52;
    var az = (Math.atan2(x, -y) * 180) / Math.PI;
    placeSun(az, 90 - (Math.min(R, Math.hypot(x, y)) / R) * 90, null);
  }
  box.addEventListener("pointerdown", function (event) {
    box.setPointerCapture(event.pointerId);
    place(event);
    box.onpointermove = place;
  });
  box.addEventListener("pointerup", function () {
    box.onpointermove = null;
  });
  return {
    el: box,
    show: function (sun) {
      var p = polar(sun.az, sun.el);
      dot.setAttribute("cx", p[0].toFixed(1));
      dot.setAttribute("cy", p[1].toFixed(1));
    },
  };
}

function slider(label: string, min: number, max: number, step: number, input: (v: number) => void) {
  var row = node("label", "sun-row");
  row.appendChild(node("span", "sun-k", label));
  var range = node("input", "");
  range.type = "range";
  range.min = String(min);
  range.max = String(max);
  range.step = String(step);
  range.addEventListener("input", function () {
    input(+range.value);
  });
  var out = node("output", "sun-v");
  row.appendChild(range);
  row.appendChild(out);
  return { row: row, range: range, out: out };
}

function toggle(label: string, key: string, read: (sun: Sun) => boolean) {
  var row = node("label", "sun-check");
  var box = node("input", "");
  box.type = "checkbox";
  box.addEventListener("change", function () {
    setSetting(key, box.checked);
  });
  row.appendChild(box);
  row.appendChild(document.createTextNode(" " + label));
  return {
    row: row,
    show: function (sun: Sun) {
      box.checked = read(sun);
    },
  };
}

function panel(): HTMLElement {
  var box = node("div", "sun-panel");
  box.hidden = true;
  var head = node("div", "sun-head");
  var title = node("span", "sun-title", "sun");
  var readout = node("span", "sun-readout");
  var reset = node("button", "sun-reset", "default");
  reset.type = "button";
  reset.title = "back to the sun Settings → map picks";
  reset.addEventListener("click", function () {
    resetSun();
  });
  head.appendChild(title);
  head.appendChild(readout);
  head.appendChild(reset);
  box.appendChild(head);
  var time = slider("time", FIRST_HOUR, LAST_HOUR, 0.05, setHour);
  box.appendChild(time.row);
  var presets = node("div", "sun-presets");
  ([["noon", NOON_HOUR], ["09:00", 9], ["16:00", 16]] as [string, number][]).forEach(function (p) {
    var b = node("button", "", p[0]);
    b.type = "button";
    b.addEventListener("click", function () {
      setHour(p[1]);
    });
    presets.appendChild(b);
  });
  var nw = node("button", "", "map NW");
  nw.type = "button";
  nw.title = "north-west at 45°, the relief-map convention";
  nw.addEventListener("click", function () {
    placeSun(MAP_NW[0], MAP_NW[1], null);
  });
  presets.appendChild(nw);
  box.appendChild(presets);
  var shadows = toggle("shadows", "sunShadows", function (s) {
    return s.shadows;
  });
  var sky = toggle("sky light", "sunSky", function (s) {
    return s.sky;
  });
  var checks = node("div", "sun-checks");
  checks.appendChild(shadows.row);
  checks.appendChild(sky.row);
  box.appendChild(checks);
  var more = node("details", "sun-more");
  more.appendChild(node("summary", "", "advanced"));
  var dial = compass();
  more.appendChild(dial.el);
  var az = slider("azimuth", 0, 360, 0.5, function (v) {
    placeSun(v, currentSun().el, null);
  });
  var el = slider("elevation", MIN_EL, 90, 0.5, function (v) {
    placeSun(currentSun().az, v, null);
  });
  more.appendChild(az.row);
  more.appendChild(el.row);
  box.appendChild(more);
  refresh = function (sun) {
    readout.textContent =
      (sun.hour !== null ? hourText(sun.hour) + " · " : "") + Math.round(sun.az) + "° / " + Math.round(sun.el) + "°";
    reset.hidden = !sunOverridden();
    time.range.value = String(sun.hour !== null ? sun.hour : NOON_HOUR);
    time.out.textContent = sun.hour !== null ? hourText(sun.hour) : "—";
    az.range.value = String(sun.az);
    az.out.textContent = Math.round(sun.az) + "°";
    el.range.value = String(sun.el);
    el.out.textContent = Math.round(sun.el) + "°";
    shadows.show(sun);
    sky.show(sun);
    dial.show(sun);
  };
  refresh(currentSun());
  return box;
}

function build(): L.Control {
  var made = new L.Control({ position: "topleft" });
  made.onAdd = function () {
    root = L.DomUtil.create("div", "leaflet-bar sun-control");
    var button = L.DomUtil.create("a", "sun-button", root);
    button.href = "#";
    button.innerHTML = "&#9728;";
    button.title = "sun: time of day and shadows";
    button.setAttribute("aria-label", "sun");
    button.setAttribute("role", "button");
    button.setAttribute("aria-expanded", "false");
    var box = panel();
    root.appendChild(box);
    L.DomEvent.disableClickPropagation(root);
    L.DomEvent.disableScrollPropagation(root);
    L.DomEvent.on(button, "click", function (event) {
      L.DomEvent.preventDefault(event);
      box.hidden = !box.hidden;
      button.setAttribute("aria-expanded", box.hidden ? "false" : "true");
    });
    return root;
  };
  return made;
}

onSun(function (sun) {
  if (refresh) refresh(sun);
});

/** Shown only while the base map is drawn with live light. */
export function showSunControl(visible: boolean): void {
  if (visible && !control) {
    control = build();
    control.addTo(map);
  } else if (!visible && control) {
    control.remove();
    control = null;
    root = null;
    refresh = null;
  }
}
