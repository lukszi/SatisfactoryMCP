/* The planner's address as the page holds it; the pure grammar is routes.ts. */

import { dashParts } from "../../app/nav";
import { parsePlannerAddress } from "./routes";

export { ALL_STAGES, altDash, buildDash, parsePlannerAddress, PLAN_TABS, planDash, versionDash } from "./routes";
export type { PlannerAddress, PlanTab } from "./routes";

/** `?item=<name>&rate=<n>`: the new-plan card filled in (Progress links its steps here). */
export function plannerDraft(at: string): { item: string; rate: string } | null {
  if (at.charAt(0) !== "?") return null;
  const params = new URLSearchParams(at.slice(1));
  return { item: params.get("item") || "", rate: params.get("rate") || "" };
}

/** The plan the address bar has open: its key, "" on the plans list, null off the planner. */
export function openPlanKey(): string | null {
  const at = dashParts();
  if (at.tab !== "planner") return null;
  return parsePlannerAddress(at.subject).key;
}
