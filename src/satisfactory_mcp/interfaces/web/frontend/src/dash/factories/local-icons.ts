/* Icon choices an older page kept in this browser (per world, under one localStorage key),
 * handed to the server once and then dropped. Pure but for the storage passed in. */

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** localStorage, or null where the browser refuses it. */
export function browserStore(): Store | null {
  try {
    return window.localStorage;
  } catch (_e) {
    return null;
  }
}

/** The choices kept for `world` under `key`, removed from the store; {} when there are none. */
export function takeLocalChoices(store: Store | null, key: string, world: string): Record<string, string> {
  if (!store) return {};
  try {
    const all: unknown = JSON.parse(store.getItem(key) || "null");
    if (!all || typeof all !== "object") return {};
    const worlds = all as Record<string, unknown>;
    const mine = worlds[world];
    if (mine === undefined) return {};
    delete worlds[world];
    if (Object.keys(worlds).length) store.setItem(key, JSON.stringify(worlds));
    else store.removeItem(key);
    if (!mine || typeof mine !== "object") return {};
    const out: Record<string, string> = {};
    Object.entries(mine as Record<string, unknown>).forEach(([id, icon]) => {
      if (typeof icon === "string") out[id] = icon;
    });
    return out;
  } catch (_e) {
    return {};
  }
}
