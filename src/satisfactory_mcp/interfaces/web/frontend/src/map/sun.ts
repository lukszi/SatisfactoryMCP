/* The sun a map drawn with live light is lit by: the game's own path through the day, the
 * default from Settings, and the map control's override for this visit.
 *
 * `gameSun` is the arithmetic of mapgen's lighting/sun.py; both put game noon at 225° / 62.25°.
 * docs/spatial-and-map.md §29. */

import { createListeners } from "../app/listeners";
import { onSetting, settingChoice, settingOn } from "../app/settings";

export interface Sun {
  azimuthDeg: number;
  elevationDeg: number;
  /** The game hour the sun stands at, or null when it was placed off the path. */
  hour: number | null;
  shadows: boolean;
  sky: boolean;
}

export const NOON_HOUR = 11.87;
export const FIRST_HOUR = 6.25;
export const LAST_HOUR = 17.5;
export const MAP_NW: [number, number] = [315, 45];
export const MIN_ELEVATION_DEG = 3;

/** The times of day the control offers as buttons, and the Settings default can name. */
export const SUN_PRESETS: { key: string; label: string; hour: number }[] = [
  { key: "noon", label: "noon", hour: NOON_HOUR },
  { key: "09:00", label: "09:00", hour: 9 },
  { key: "16:00", label: "16:00", hour: 16 },
];

type Matrix = number[][];

function rotator(pitch: number, yaw: number, roll: number): Matrix {
  const toRadians = Math.PI / 180;
  const sp = Math.sin(pitch * toRadians), cp = Math.cos(pitch * toRadians);
  const sy = Math.sin(yaw * toRadians), cy = Math.cos(yaw * toRadians);
  const sr = Math.sin(roll * toRadians), cr = Math.cos(roll * toRadians);
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

const AXIS = rotator(0, 45, 25);
const LIGHT = (function () {
  const light = timesCol(AXIS, rotator(55, 190, 0)[0]!);
  const length = Math.hypot(light[0]!, light[1]!, light[2]!);
  return light.map(function (component) {
    return component / length;
  });
})();

/** The sun at a game hour, as [azimuth from north, elevation], degrees. */
export function gameSun(hour: number): [number, number] {
  const toSun = rowTimes(rowTimes(LIGHT, rotator(-(30 + 15 * hour), 0, 0)), AXIS).map(function (component) {
    return -component;
  });
  const azimuthDeg = ((Math.atan2(toSun[0]!, -toSun[1]!) * 180) / Math.PI + 360) % 360;
  const elevationDeg = (Math.asin(toSun[2]! / Math.hypot(toSun[0]!, toSun[1]!, toSun[2]!)) * 180) / Math.PI;
  return [azimuthDeg, elevationDeg];
}

export function hourText(hour: number): string {
  let hours = Math.floor(hour);
  let minutes = Math.round((hour - hours) * 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  return (hours < 10 ? "0" : "") + hours + ":" + (minutes < 10 ? "0" : "") + minutes;
}

interface Place {
  azimuthDeg: number;
  elevationDeg: number;
  hour: number | null;
}

function presetPlace(key: string): Place {
  if (key === "nw") return { azimuthDeg: MAP_NW[0], elevationDeg: MAP_NW[1], hour: null };
  const preset = SUN_PRESETS.find(function (candidate) {
    return candidate.key === key;
  });
  const hour = preset ? preset.hour : NOON_HOUR;
  const at = gameSun(hour);
  return { azimuthDeg: at[0], elevationDeg: at[1], hour: hour };
}

/* Where the map control put the sun for this visit; null follows Settings. Shadows and sky are
 * settings either way, so the control's toggles are remembered like any other. */
let override: Place | null = null;
const listeners = createListeners<[Sun]>();

export function currentSun(): Sun {
  const at = override || presetPlace(settingChoice("sunTime") || "noon");
  return {
    azimuthDeg: at.azimuthDeg,
    elevationDeg: at.elevationDeg,
    hour: at.hour,
    shadows: settingOn("sunShadows"),
    sky: settingOn("sunSky"),
  };
}

/** Whether the map control has moved the sun away from the Settings default. */
export function sunOverridden(): boolean {
  return override !== null;
}

function tell(): void {
  listeners.emit(currentSun());
}

export const onSun = listeners.on;

/** Move the sun for this visit; Settings keeps the default. */
export function placeSun(azimuthDeg: number, elevationDeg: number, hour: number | null): void {
  override = {
    azimuthDeg: ((azimuthDeg % 360) + 360) % 360,
    elevationDeg: Math.max(MIN_ELEVATION_DEG, Math.min(90, elevationDeg)),
    hour: hour,
  };
  tell();
}

export function setHour(hour: number): void {
  const at = gameSun(hour);
  placeSun(at[0], at[1], hour);
}

export function resetSun(): void {
  override = null;
  tell();
}

onSetting(tell);
