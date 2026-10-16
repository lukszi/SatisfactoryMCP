/* The route table over the fragment's `dash=` value: which page, which top tab is lit, and
 * the old addresses that now land elsewhere. Pure, so it is tested without a page. */

export type Page = "map" | "overview" | "factories" | "power" | "progress" | "inventory" | "recipes" | "world" | "planner" | "maps" | "kit";

export type TopTab = "map" | "overview" | "factories" | "power" | "progress" | "items" | "world" | "planner";

export interface TabLink {
  id: TopTab;
  label: string;
  dash: string;
}

/** The top bar, in order; Items opens the inventory page until Items has its own. */
export const TOP_TABS: TabLink[] = [
  { id: "map", label: "Map", dash: "" },
  { id: "overview", label: "Overview", dash: "overview" },
  { id: "factories", label: "Factories", dash: "factories" },
  { id: "power", label: "Power", dash: "power" },
  { id: "progress", label: "Progress", dash: "progress" },
  { id: "items", label: "Items", dash: "inventory" },
  { id: "world", label: "World", dash: "world" },
  { id: "planner", label: "Planner", dash: "planner" },
];

/** The phone bottom bar; the rest sit in its More sheet. */
export const PHONE_TABS: TopTab[] = ["map", "overview", "factories", "planner"];

export interface Route {
  page: Page;
  subject: string;
  top: TopTab | "";
}

/** Where an old or shorthand address goes; `settings` opens the dialog over the page. */
export interface Redirect {
  dash: string | null;
  settings?: string;
}

const TOP_OF: Record<Page, TopTab | ""> = {
  map: "map",
  overview: "overview",
  factories: "factories",
  power: "power",
  progress: "progress",
  inventory: "items",
  recipes: "items",
  world: "world",
  planner: "planner",
  maps: "map",
  kit: "",
};

function split(dash: string): [string, string] {
  const cut = dash.indexOf("/");
  return cut < 0 ? [dash, ""] : [dash.slice(0, cut), dash.slice(cut + 1)];
}

/** null when the address is current; `dash: null` keeps the page under the dialog. */
export function redirectFor(dash: string): Redirect | null {
  const [head, subject] = split(dash);
  if (head === "settings") return subject === "maps" ? { dash: "maps" } : { dash: null, settings: "" };
  if (head === "items" && !subject) return { dash: "inventory" };
  return null;
}

export function routeOf(dash: string): Route {
  if (!dash) return { page: "map", subject: "", top: "map" };
  const [head, subject] = split(dash);
  const page = (head in TOP_OF && head !== "map" ? head : "overview") as Page;
  return { page: page, subject: page === head ? subject : "", top: TOP_OF[page] };
}

/** The settings dialog's section from the fragment: null = closed, "" = the last one shown. */
export function settingsSection(raw: string | undefined, sections: string[]): string | null {
  if (raw === undefined) return null;
  return sections.indexOf(raw) >= 0 ? raw : "";
}
