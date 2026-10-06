/* The sun a map drawn with live light is lit by: the game's own path through the day, the
 * default from Settings, and the map control's override for this visit.
 *
 * `gameSun` is the arithmetic of mapgen's lighting/sun.py; both put game noon at 225° / 62.25°.
 * docs/spatial-and-map.md §29. */

import { choice, onSetting, setting } from "../app/settings";

export interface Sun {
  az: number;
  el: number;
  /** The game hour the sun stands at, or null when it was placed off the path. */
  hour: number | null;
  shadows: boolean;
  sky: boolean;
}

export var NOON_HOUR = 11.87;
export var FIRST_HOUR = 6.25;
export var LAST_HOUR = 17.5;
export var MAP_NW: [number, number] = [315, 45];
export var MIN_EL = 3;

type Matrix = number[][];

function rotator(pitch: number, yaw: number, roll: number): Matrix {
  var d = Math.PI / 180;
  var sp = Math.sin(pitch * d), cp = Math.cos(pitch * d);
  var sy = Math.sin(yaw * d), cy = Math.cos(yaw * d);
  var sr = Math.sin(roll * d), cr = Math.cos(roll * d);
  return [
    [cp * cy, cp * sy, sp],
    [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
    [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
  ];
}

function rowTimes(v: number[], m: Matrix): number[] {
  return [0, 1, 2].map(function (j) {
    return v[0]! * m[0]![j]! + v[1]! * m[1]![j]! + v[2]! * m[2]![j]!;
  });
}

function timesCol(m: Matrix, v: number[]): number[] {
  return m.map(function (row) {
    return row[0]! * v[0]! + row[1]! * v[1]! + row[2]! * v[2]!;
  });
}

var AXIS = rotator(0, 45, 25);
var LIGHT = (function () {
  var l = timesCol(AXIS, rotator(55, 190, 0)[0]!);
  var n = Math.hypot(l[0]!, l[1]!, l[2]!);
  return l.map(function (c) {
    return c / n;
  });
})();

/** The sun at a game hour, as [azimuth from north, elevation], degrees. */
export function gameSun(hour: number): [number, number] {
  var s = rowTimes(rowTimes(LIGHT, rotator(-(30 + 15 * hour), 0, 0)), AXIS).map(function (c) {
    return -c;
  });
  var az = ((Math.atan2(s[0]!, -s[1]!) * 180) / Math.PI + 360) % 360;
  var el = (Math.asin(s[2]! / Math.hypot(s[0]!, s[1]!, s[2]!)) * 180) / Math.PI;
  return [az, el];
}

export function hourText(hour: number): string {
  var h = Math.floor(hour);
  var m = Math.round((hour - h) * 60);
  if (m === 60) {
    h += 1;
    m = 0;
  }
  return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
}

interface Place {
  az: number;
  el: number;
  hour: number | null;
}

function presetPlace(key: string): Place {
  if (key === "nw") return { az: MAP_NW[0], el: MAP_NW[1], hour: null };
  var hour = key === "09:00" ? 9 : key === "16:00" ? 16 : NOON_HOUR;
  var at = gameSun(hour);
  return { az: at[0], el: at[1], hour: hour };
}

/* Where the map control put the sun for this visit; null follows Settings. Shadows and sky are
 * settings either way, so the control's toggles are remembered like any other. */
var override: Place | null = null;
var listeners: Array<(sun: Sun) => void> = [];

export function currentSun(): Sun {
  var at = override || presetPlace(choice("sunTime") || "noon");
  return { az: at.az, el: at.el, hour: at.hour, shadows: setting("sunShadows"), sky: setting("sunSky") };
}

/** Whether the map control has moved the sun away from the Settings default. */
export function sunOverridden(): boolean {
  return override !== null;
}

function tell(): void {
  var sun = currentSun();
  listeners.forEach(function (listener) {
    listener(sun);
  });
}

export function onSun(listener: (sun: Sun) => void): void {
  listeners.push(listener);
}

/** Move the sun for this visit; Settings keeps the default. */
export function placeSun(az: number, el: number, hour: number | null): void {
  override = { az: ((az % 360) + 360) % 360, el: Math.max(MIN_EL, Math.min(90, el)), hour: hour };
  tell();
}

export function setHour(hour: number): void {
  var at = gameSun(hour);
  placeSun(at[0], at[1], hour);
}

export function resetSun(): void {
  override = null;
  tell();
}

onSetting(tell);
