/* The pure half of the in-game map markers: which casing a marker's own colour needs to stay
 * visible. No Leaflet, so vitest can hold it. Drawn by map-markers.ts. */

/** Relative luminance of `#rrggbb` (sRGB, WCAG weights), 0 for black and 1 for white. */
export function luminance(hex: string): number {
  const channel = function (at: number): number {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** The light casing on a dark marker and the dark one on a light marker, split where the two
 *  contrast ratios against black and white cross. A colour that is not `#rrggbb` is cased
 *  light, as the game's default grey would be. */
export function casingFor(hex: string | null | undefined, light: string, dark: string): string {
  if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) return light;
  return luminance(hex) > 0.179 ? dark : light;
}
