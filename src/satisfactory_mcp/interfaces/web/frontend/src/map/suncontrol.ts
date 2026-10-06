/* The sun button: a small control on the map while a lit layer is drawn. It opens a time-of-day
 * slider on the game's sun path, four presets, the shadow and sky switches, and under
 * "advanced" a free compass. It moves the sun for this visit; Settings → map keeps the default. */

import { make } from "../kit/dom";
import { L } from "./leaflet";
import { map } from "./map";
import { setSetting } from "../app/settings";
import {
  currentSun,
  FIRST_HOUR,
  gameSun,
  hourText,
  LAST_HOUR,
  MAP_NW,
  MIN_ELEVATION_DEG,
  NOON_HOUR,
  onSun,
  placeSun,
  resetSun,
  setHour,
  SUN_PRESETS,
  sunOverridden,
} from "./sun";

import type { Sun } from "./sun";

/** The compass dial's radius, in its own SVG units: the horizon ring. */
var COMPASS_RADIUS_PX = 46;
var control: L.Control | null = null;
var root: HTMLElement | null = null;
var refresh: ((sun: Sun) => void) | null = null;

var SVG = "http://www.w3.org/2000/svg";

function svg(tag: string, attrs: Record<string, string | number>): SVGElement {
  const made = document.createElementNS(SVG, tag) as SVGElement;
  Object.keys(attrs).forEach(function (name) {
    made.setAttribute(name, String(attrs[name]));
  });
  return made;
}

/** Where a sun position sits on the dial: the zenith at the centre, the horizon on the ring. */
function polar(azimuthDeg: number, elevationDeg: number): [number, number] {
  const radius = ((90 - elevationDeg) / 90) * COMPASS_RADIUS_PX;
  const angle = (azimuthDeg * Math.PI) / 180;
  return [radius * Math.sin(angle), -radius * Math.cos(angle)];
}

function compass(): { el: SVGElement; show: (sun: Sun) => void } {
  const box = svg("svg", { viewBox: "-52 -52 104 104", width: 104, height: 104, class: "sun-compass", role: "img" });
  box.setAttribute("aria-label", "sun position: drag to move the sun anywhere");
  box.appendChild(svg("circle", { r: COMPASS_RADIUS_PX, class: "sun-ring" }));
  box.appendChild(svg("circle", { r: COMPASS_RADIUS_PX / 2, class: "sun-ring-in" }));
  const points: string[] = [];
  for (let hour = FIRST_HOUR; hour <= LAST_HOUR; hour += 0.1) {
    const at = gameSun(hour);
    if (at[1] < 0) continue;
    const point = polar(at[0], at[1]);
    points.push(point[0].toFixed(1) + "," + point[1].toFixed(1));
  }
  box.appendChild(svg("path", { d: "M" + points.join("L"), class: "sun-path" }));
  ([
    ["N", 0, -COMPASS_RADIUS_PX + 9],
    ["E", COMPASS_RADIUS_PX - 7, 3],
    ["S", 0, COMPASS_RADIUS_PX - 3],
    ["W", -COMPASS_RADIUS_PX + 7, 3],
  ] as [string, number, number][]).forEach(function (cardinal) {
    const label = svg("text", { x: cardinal[1], y: cardinal[2], class: "sun-cardinal" });
    label.textContent = cardinal[0];
    box.appendChild(label);
  });
  const dot = svg("circle", { r: 5, class: "sun-dot" });
  box.appendChild(dot);
  function place(event: PointerEvent): void {
    const rect = box.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 104 - 52;
    const y = ((event.clientY - rect.top) / rect.height) * 104 - 52;
    const azimuthDeg = (Math.atan2(x, -y) * 180) / Math.PI;
    placeSun(azimuthDeg, 90 - (Math.min(COMPASS_RADIUS_PX, Math.hypot(x, y)) / COMPASS_RADIUS_PX) * 90, null);
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
      const point = polar(sun.azimuthDeg, sun.elevationDeg);
      dot.setAttribute("cx", point[0].toFixed(1));
      dot.setAttribute("cy", point[1].toFixed(1));
    },
  };
}

function slider(label: string, min: number, max: number, step: number, input: (value: number) => void) {
  const row = make("label", "sun-row");
  row.appendChild(make("span", "sun-k", label));
  const range = make("input", "");
  range.type = "range";
  range.min = String(min);
  range.max = String(max);
  range.step = String(step);
  range.addEventListener("input", function () {
    input(+range.value);
  });
  const out = make("output", "sun-v");
  row.appendChild(range);
  row.appendChild(out);
  return { row: row, range: range, out: out };
}

function toggle(label: string, key: string, read: (sun: Sun) => boolean) {
  const row = make("label", "sun-check");
  const box = make("input", "");
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
  const box = make("div", "sun-panel");
  box.hidden = true;
  const head = make("div", "sun-head");
  const title = make("span", "sun-title", "sun");
  const readout = make("span", "sun-readout");
  const reset = make("button", "sun-reset", "default");
  reset.type = "button";
  reset.title = "back to the sun Settings → map picks";
  reset.addEventListener("click", function () {
    resetSun();
  });
  head.appendChild(title);
  head.appendChild(readout);
  head.appendChild(reset);
  box.appendChild(head);
  const time = slider("time", FIRST_HOUR, LAST_HOUR, 0.05, setHour);
  box.appendChild(time.row);
  const presets = make("div", "sun-presets");
  SUN_PRESETS.forEach(function (preset) {
    const presetButton = make("button", "", preset.label);
    presetButton.type = "button";
    presetButton.addEventListener("click", function () {
      setHour(preset.hour);
    });
    presets.appendChild(presetButton);
  });
  const northWest = make("button", "", "map NW");
  northWest.type = "button";
  northWest.title = "north-west at 45°, the relief-map convention";
  northWest.addEventListener("click", function () {
    placeSun(MAP_NW[0], MAP_NW[1], null);
  });
  presets.appendChild(northWest);
  box.appendChild(presets);
  const shadows = toggle("shadows", "sunShadows", function (sun) {
    return sun.shadows;
  });
  const sky = toggle("sky light", "sunSky", function (sun) {
    return sun.sky;
  });
  const checks = make("div", "sun-checks");
  checks.appendChild(shadows.row);
  checks.appendChild(sky.row);
  box.appendChild(checks);
  const more = make("details", "sun-more");
  more.appendChild(make("summary", "", "advanced"));
  const dial = compass();
  more.appendChild(dial.el);
  const azimuth = slider("azimuth", 0, 360, 0.5, function (value) {
    placeSun(value, currentSun().elevationDeg, null);
  });
  const elevation = slider("elevation", MIN_ELEVATION_DEG, 90, 0.5, function (value) {
    placeSun(currentSun().azimuthDeg, value, null);
  });
  more.appendChild(azimuth.row);
  more.appendChild(elevation.row);
  box.appendChild(more);
  refresh = function (sun) {
    readout.textContent =
      (sun.hour !== null ? hourText(sun.hour) + " · " : "") + Math.round(sun.azimuthDeg) + "° / " + Math.round(sun.elevationDeg) + "°";
    reset.hidden = !sunOverridden();
    time.range.value = String(sun.hour !== null ? sun.hour : NOON_HOUR);
    time.out.textContent = sun.hour !== null ? hourText(sun.hour) : "—";
    azimuth.range.value = String(sun.azimuthDeg);
    azimuth.out.textContent = Math.round(sun.azimuthDeg) + "°";
    elevation.range.value = String(sun.elevationDeg);
    elevation.out.textContent = Math.round(sun.elevationDeg) + "°";
    shadows.show(sun);
    sky.show(sun);
    dial.show(sun);
  };
  refresh(currentSun());
  return box;
}

function build(): L.Control {
  const made = new L.Control({ position: "topleft" });
  made.onAdd = function () {
    root = L.DomUtil.create("div", "leaflet-bar sun-control");
    const button = L.DomUtil.create("a", "sun-button", root);
    button.href = "#";
    button.innerHTML = "&#9728;";
    button.title = "sun: time of day and shadows";
    button.setAttribute("aria-label", "sun");
    button.setAttribute("role", "button");
    button.setAttribute("aria-expanded", "false");
    const box = panel();
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
