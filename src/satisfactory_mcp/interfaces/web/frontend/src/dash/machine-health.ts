/* Machines that need action, grouped as the Overview's "do next" and the map's side panel list
 * them. Pure. */

import { WORDS } from "../kit/words";

import type { MachineIssue } from "../api/shapes";

export interface IssueGroup {
  factory: string;
  state: string;
  what: string;
  cause: string;
  issues: MachineIssue[];
}

function issueCauseText(issue: MachineIssue): string {
  if (!issue.cause.length) return "";
  const items = issue.cause.join(", ");
  if (issue.state === WORDS.blocked) return "can't output " + items;
  if (issue.state === "starved") return "short of " + items;
  return items;
}

/* One row per factory, state, machine kind and cause. */
export function issueGroups(rows: { name: string; worst_actionable: MachineIssue[] }[]): IssueGroup[] {
  const found: IssueGroup[] = [];
  rows.forEach(function (row) {
    row.worst_actionable.forEach(function (issue) {
      const cause = issueCauseText(issue);
      const same = found.filter(function (group) {
        return group.factory === row.name && group.state === issue.state && group.what === issue.what && group.cause === cause;
      })[0];
      if (same) same.issues.push(issue);
      else found.push({ factory: row.name, state: issue.state, what: issue.what, cause: cause, issues: [issue] });
    });
  });
  return found;
}

export function issueCount(groups: IssueGroup[]): number {
  let total = 0;
  groups.forEach(function (group) {
    total += group.issues.length;
  });
  return total;
}
