/* The Items page's address, `items?view=held|all|containers&item=<key>&box=<id>&rec=<key>&locked=1`,
 * and where the old Inventory and Recipes addresses land. Pure. */

export type View = "held" | "all" | "containers";

export interface ItemsAddress {
  view: View;
  /** An item's slug or class. */
  item: string;
  /** A container's or crate's instance id. */
  box: string;
  /** The marked recipe's slug or class. */
  rec: string;
  locked: boolean;
}

export const VIEWS: View[] = ["held", "all", "containers"];

/** A name as its address key: "Reinforced Iron Plate" -> "reinforced-iron-plate". */
export function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function decoded(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch (_e) {
    return text;
  }
}

/** `subject` is what follows "items": "" or "?view=…". */
export function itemsAddress(subject: string): ItemsAddress {
  const params: Record<string, string> = {};
  const cut = subject.indexOf("?");
  (cut < 0 ? "" : subject.slice(cut + 1)).split("&").forEach(function (pair) {
    const eq = pair.indexOf("=");
    if (eq > 0) params[pair.slice(0, eq)] = decoded(pair.slice(eq + 1));
  });
  const view = VIEWS.indexOf(params.view as View) >= 0 ? (params.view as View) : "held";
  const box = view === "containers" ? params.box || "" : "";
  const item = view === "containers" ? "" : params.item || "";
  return { view, item, box, rec: item || view === "all" ? params.rec || "" : "", locked: params.locked === "1" && view === "all" };
}

export function itemsDash(a: Partial<ItemsAddress>): string {
  const view = a.view || "held";
  const parts = ["view=" + view];
  if (a.item && view !== "containers") parts.push("item=" + encodeURIComponent(a.item));
  if (a.box && view === "containers") parts.push("box=" + encodeURIComponent(a.box));
  if (a.rec && view !== "containers") parts.push("rec=" + encodeURIComponent(a.rec));
  if (a.locked && view === "all") parts.push("locked=1");
  return "items?" + parts.join("&");
}

/** Where an old Inventory or Recipes address lands; null when `head` is not one of them. */
export function legacyItemsDash(head: string, subject: string): string | null {
  if (head === "inventory") return itemsDash({ view: "held", item: subject ? slug(decoded(subject)) : "" });
  if (head === "inventory-boosts") return itemsDash({ view: "held" });
  if (head === "recipes") {
    const cut = subject.indexOf("/");
    const kind = cut < 0 ? "" : subject.slice(0, cut);
    const id = cut < 0 ? "" : subject.slice(cut + 1);
    if (kind === "item" && id) return itemsDash({ view: "all", item: id });
    if (kind === "recipe" && id) return itemsDash({ view: "all", rec: id });
    return itemsDash({ view: "all" });
  }
  if (head.indexOf("item-") === 0 && head.length > 5) return itemsDash({ view: "all", item: head.slice(5) });
  if (head.indexOf("recipe-") === 0 && head.length > 7) return itemsDash({ view: "all", rec: head.slice(7) });
  return null;
}
