import { afterEach, describe, expect, it, vi } from "vitest";

import { resetSettings, setSetting } from "../../src/app/settings";
import {
  currentSun,
  FIRST_HOUR,
  gameSun,
  hillshadeOnly,
  hourText,
  isHillshadeOnly,
  LAST_HOUR,
  MAP_NW,
  MIN_ELEVATION_DEG,
  NOON_HOUR,
  onSun,
  placeSun,
  resetSun,
  setHour,
  sunOverridden,
} from "../../src/map/sun";

import type { Sun } from "../../src/map/sun";

afterEach(() => {
  resetSun();
  resetSettings();
});

describe("gameSun", () => {
  it("puts game noon at 225° / 62.25°, as mapgen's sun.py does", () => {
    const [azimuth, elevation] = gameSun(NOON_HOUR);
    expect(Math.abs(azimuth - 225)).toBeLessThan(0.1);
    expect(Math.abs(elevation - 62.25)).toBeLessThan(0.05);
  });

  it("is lower in the morning and the afternoon, and still up", () => {
    const noon = gameSun(NOON_HOUR)[1];
    for (const hour of [FIRST_HOUR, 9, 16, LAST_HOUR]) {
      const [azimuth, elevation] = gameSun(hour);
      expect(elevation).toBeLessThan(noon);
      expect(elevation).toBeGreaterThan(0);
      expect(azimuth).toBeGreaterThanOrEqual(0);
      expect(azimuth).toBeLessThan(360);
    }
  });
});

describe("hourText", () => {
  it("prints a game hour as a clock time, carrying a rounded-up minute", () => {
    expect(hourText(9)).toBe("09:00");
    expect(hourText(NOON_HOUR)).toBe("11:52");
    expect(hourText(16.999)).toBe("17:00");
  });
});

describe("the sun the page is lit by", () => {
  it("follows the Settings default until the control moves it", () => {
    const sun = currentSun();
    expect(sun.hour).toBe(NOON_HOUR);
    expect(sun.azimuthDeg).toBeCloseTo(gameSun(NOON_HOUR)[0], 9);
    expect(sun).toMatchObject({ shade: true, terrainShadows: true, treeShadows: true, sky: true });
    expect(sunOverridden()).toBe(false);
  });

  it("reads the preset Settings names, and the map's north-west", () => {
    setSetting("sunTime", "09:00");
    expect(currentSun().hour).toBe(9);
    setSetting("sunTime", "nw");
    expect(currentSun()).toMatchObject({ azimuthDeg: MAP_NW[0], elevationDeg: MAP_NW[1], hour: null });
  });

  it("reads each switch from its own setting", () => {
    setSetting("sunShadows", false);
    expect(currentSun()).toMatchObject({ shade: true, terrainShadows: false, treeShadows: true, sky: true });
    setSetting("sunTreeShadows", false);
    expect(currentSun()).toMatchObject({ terrainShadows: false, treeShadows: false });
    setSetting("mapShade", false);
    expect(currentSun().shade).toBe(false);
    expect(currentSun().trees).toBe(true);
    setSetting("mapTrees", false);
    expect(currentSun().trees).toBe(false);
  });

  it("normalises the azimuth and keeps the sun above the horizon and below the zenith", () => {
    placeSun(-30, 120, null);
    expect(currentSun()).toMatchObject({ azimuthDeg: 330, elevationDeg: 90, hour: null });
    placeSun(370, 1, 9);
    expect(currentSun()).toMatchObject({ azimuthDeg: 10, elevationDeg: MIN_ELEVATION_DEG, hour: 9 });
    expect(sunOverridden()).toBe(true);
  });

  it("tells its listeners on every move, reset and settings change", () => {
    const heard: Sun[] = [];
    const listener = vi.fn((sun: Sun) => heard.push(sun));
    onSun(listener);
    setHour(16);
    expect(heard.at(-1)).toMatchObject({ hour: 16, azimuthDeg: gameSun(16)[0] });
    resetSun();
    expect(heard.at(-1)!.hour).toBe(NOON_HOUR);
    setSetting("sunSky", false);
    expect(heard.at(-1)!.sky).toBe(false);
    expect(listener).toHaveBeenCalledTimes(3);
  });
});

describe("hillshade only", () => {
  it("turns both shadows and the sky off, the shade on, and tells once", () => {
    setSetting("mapShade", false);
    const listener = vi.fn();
    onSun(listener);
    const before = currentSun();
    hillshadeOnly();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(currentSun()).toMatchObject({ shade: true, terrainShadows: false, treeShadows: false, sky: false });
    expect(currentSun().azimuthDeg).toBe(before.azimuthDeg);
    expect(isHillshadeOnly(currentSun(), true)).toBe(true);
  });

  it("is that look only while every switch the map has agrees", () => {
    hillshadeOnly();
    setSetting("sunTreeShadows", true);
    expect(isHillshadeOnly(currentSun(), true)).toBe(false);
    expect(isHillshadeOnly(currentSun(), false)).toBe(true);
    setSetting("sunSky", true);
    expect(isHillshadeOnly(currentSun(), false)).toBe(false);
    hillshadeOnly();
    setSetting("mapShade", false);
    expect(isHillshadeOnly(currentSun(), true)).toBe(false);
  });
});
