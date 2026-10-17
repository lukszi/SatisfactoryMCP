/* A plan's addresses, `planner/<key>[/…]`: reading them apart, building them, and where the old
 * ones land. Pure. docs/frontend_kit.md, "Plan page". */

export type PlanTab = "machines" | "flows" | "site" | "build" | "versions";

/** The plan page's tabs in lifecycle order, with their labels. */
export const PLAN_TABS: [PlanTab, string][] = [
  ["machines", "machines"],
  ["flows", "flows"],
  ["site", "site"],
  ["build", "to build"],
  ["versions", "versions"],
];

/** A stage on the to build tab: 0 the current one, ALL_STAGES every stage. */
export const ALL_STAGES = -1;

export interface PlannerAddress {
  key: string;
  /** "" for the bare address, which lands on the plan's current step. */
  tab: PlanTab | "";
  viewedRev: number;
  alternatesItem: string;
  /** The pad editor beside the main map. */
  place: boolean;
  stage: number;
  /** The to build tab asks which factory counts as built. */
  pick: boolean;
}

function isTab(word: string): word is PlanTab {
  return PLAN_TABS.some((t) => t[0] === word);
}

function params(query: string): Record<string, string> {
  const out: Record<string, string> = {};
  query.split("&").forEach(function (pair) {
    const eq = pair.indexOf("=");
    if (pair) out[eq < 0 ? pair : pair.slice(0, eq)] = eq < 0 ? "" : pair.slice(eq + 1);
  });
  return out;
}

/** `<key>[/<tab>[?stage=<n|all>&pick=1] | /v<n> | /alt/<item> | /place]`, after `planner/`. */
export function parsePlannerAddress(at: string): PlannerAddress {
  const rest = at ? at.split("/") : [];
  const key = (rest[0] || "").split("?")[0]!;
  const cut = (rest[1] || "").indexOf("?");
  const head = cut < 0 ? rest[1] || "" : rest[1]!.slice(0, cut);
  const query = params(cut < 0 ? "" : rest[1]!.slice(cut + 1));
  const version = /^v(\d+)$/.exec(head);
  const item = head === "alt" && rest[2] ? rest.slice(2).join("/") : "";
  const stage = query.stage === "all" ? ALL_STAGES : /^\d+$/.test(query.stage || "") ? Number(query.stage) : 0;
  let tab: PlanTab | "" = isTab(head) ? head : "";
  if (version || item) tab = "machines";
  if (head === "place") tab = "site";
  return { key, tab, viewedRev: version ? Number(version[1]) : 0, alternatesItem: item, place: head === "place", stage, pick: query.pick === "1" };
}

export function planDash(key: string, tab?: PlanTab | "place" | ""): string {
  return "planner/" + key + (tab ? "/" + tab : "");
}

export function altDash(key: string, item: string): string {
  return "planner/" + key + "/alt/" + item;
}

export function versionDash(key: string, rev: number): string {
  return "planner/" + key + "/v" + rev;
}

export function buildDash(key: string, stage: number, pick?: boolean): string {
  const query = [stage === ALL_STAGES ? "stage=all" : stage > 0 ? "stage=" + stage : "", pick ? "pick=1" : ""].filter(Boolean);
  return planDash(key, "build") + (query.length ? "?" + query.join("&") : "");
}

/** Old plan addresses: `graph` became flows, `track[/<n>]` to build. */
export function legacyPlannerDash(head: string, subject: string): string | null {
  if (head !== "planner") return null;
  const rest = subject.split("/");
  if (rest.length < 2 || !rest[0]) return null;
  if (rest[1] === "graph") return planDash(rest[0], "flows");
  if (rest[1] === "track") return buildDash(rest[0], /^\d+$/.test(rest[2] || "") ? Number(rest[2]) : 0);
  return null;
}
