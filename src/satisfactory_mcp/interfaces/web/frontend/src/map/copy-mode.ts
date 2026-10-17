/* Which base picture a mini map copies; pure, so tiles.ts keeps the probes and this the rule. */

/** The main map's picture, else the registry default, else the artwork, else the first one
 *  that can be drawn; null only when none can (plain is never in `drawable`). */
export function pickCopyMode(current: string, chosen: string | null, artwork: string, drawable: string[]): string | null {
  const can = (mode: string | null): mode is string => !!mode && drawable.indexOf(mode) >= 0;
  if (can(current)) return current;
  if (can(chosen)) return chosen;
  if (can(artwork)) return artwork;
  return drawable[0] ?? null;
}
