/* The history rules of docs/frontend_kit.md (Navigation), as pure decisions: what each entry
 * remembers, and whether a close or a breadcrumb steps back or moves on. app/nav.ts acts. */

/** What this site writes into `history.state`: the entry's id, the dash it was opened from
 *  (null for the first entry of a visit) and the #dash scroll last seen on it. */
export interface Stamp {
  nav: string;
  from: string | null;
  scroll: number;
}

export function stampOf(raw: unknown): Stamp | null {
  const s = raw as Partial<Stamp> | null;
  if (!s || typeof s !== "object" || typeof s.nav !== "string") return null;
  return { nav: s.nav, from: typeof s.from === "string" ? s.from : null, scroll: typeof s.scroll === "number" ? s.scroll : 0 };
}

/** fresh: an entry nobody stamped yet (a link, a typed address); own: the entry this page
 *  just wrote; traverse: Back or Forward onto an older entry. */
export type Arrival = "fresh" | "own" | "traverse";

export function arrivalOf(stamp: Stamp | null, current: string): Arrival {
  if (!stamp) return "fresh";
  return stamp.nav === current ? "own" : "traverse";
}

/** A close (×, Esc, "back to …") steps back only onto the parent it was opened from. */
export function closeMove(stamp: Stamp | null, parent: string): "back" | "replace" {
  return stamp && stamp.from === parent ? "back" : "replace";
}

/** A breadcrumb steps back when the entry before is that parent, else opens it anew. */
export function crumbMove(stamp: Stamp | null, parent: string): "back" | "push" {
  return stamp && stamp.from === parent ? "back" : "push";
}
