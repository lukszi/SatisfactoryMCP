/* Machines that need action, grouped as the dashboard lists them, and the state mix bar the
 * Overview and the Factories tab both draw. */

import { link, table } from "../kit/dashkit";
import { make } from "../kit/dom";
import { count, joinWithConjunction } from "../kit/format";
import { vitals } from "../app/vitals";
import { isFine, needsAction, stateSets, stateTone } from "./machine-states";
import { WORDS } from "../kit/words";

import type { Column } from "../kit/dashkit";
import type { FactoryHealthRow, MachineIssue } from "../api/shapes";

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

export function issueTable(
  groups: IssueGroup[],
  place: (group: IssueGroup) => HTMLElement,
  withFactory: boolean
): HTMLElement {
  const columns: Column<IssueGroup>[] = [
    {
      key: "state",
      label: "state",
      className: "dash-nowrap",
      tone: function (group) {
        return stateTone(group.state, true);
      },
      render: function (group) {
        return group.state;
      },
    },
    {
      key: "n",
      label: "machines",
      align: "right",
      render: function (group) {
        return count(group.issues.length);
      },
    },
    {
      key: "what",
      label: "machine",
      className: "dash-wide",
      render: function (group) {
        if (!group.cause) return group.what;
        const cell = make("span", "", group.what);
        cell.appendChild(make("span", "dash-sub", group.cause));
        return cell;
      },
    },
  ];
  if (withFactory) {
    columns.push({
      key: "factory",
      label: WORDS.factory,
      render: function (group) {
        const anchor = link("factories/" + group.factory, group.factory, "dash-trunc");
        anchor.title = group.factory;
        return anchor;
      },
    });
  }
  columns.push({ key: "map", label: "", align: "right", render: place });
  return table<IssueGroup>(columns, groups, {
    rowTitle: function (group) {
      return group.issues
        .map(function (issue) {
          return issue.instance;
        })
        .join(", ");
    },
    caption: "machines that " + WORDS.needAction,
  });
}

export interface Mix {
  bad: number;
  blocked: number;
  mid: number;
  ok: number;
}

function middleStates(): string[] {
  const health = vitals().health;
  return (health ? health.states : []).filter(function (state) {
    return !needsAction(state) && !isFine(state);
  });
}

export function mixOf(rows: FactoryHealthRow[]): Mix {
  const mix = { bad: 0, blocked: 0, mid: 0, ok: 0 };
  rows.forEach(function (row) {
    row.states.forEach(function (entry) {
      mix[stateTone(entry.state)] += entry.count;
    });
  });
  return mix;
}

function mixWords(): [keyof Mix, string][] {
  return [
    [
      "bad",
      joinWithConjunction(
        stateSets().actionable.filter(function (state) {
          return state !== WORDS.blocked;
        }),
        "or"
      ),
    ],
    ["blocked", WORDS.blocked],
    ["mid", joinWithConjunction(middleStates(), "or")],
    ["ok", "running or unmonitored"],
  ];
}

export function mixBar(mix: Mix): HTMLElement {
  const total = mix.bad + mix.blocked + mix.mid + mix.ok;
  const bar = make("div", "dash-mix");
  bar.setAttribute("role", "img");
  const words: string[] = [];
  mixWords().forEach(function (entry) {
    const machines = mix[entry[0]];
    words.push(count(machines) + " " + entry[1]);
    if (!machines || !total) return;
    const segment = make("span", "dash-mix-" + entry[0]);
    segment.style.width = (machines / total) * 100 + "%";
    bar.appendChild(segment);
  });
  bar.title = words.join(", ");
  bar.setAttribute("aria-label", bar.title);
  return bar;
}

export function mixLegend(mix: Mix): HTMLElement {
  const legend = make("div", "dash-mix-legend");
  legend.setAttribute("aria-hidden", "true");
  mixWords().forEach(function (entry) {
    const item = make("span", "dash-key");
    item.appendChild(make("i", "dash-swatch dash-mix-" + entry[0]));
    item.appendChild(document.createTextNode(count(mix[entry[0]]) + " " + entry[1]));
    legend.appendChild(item);
  });
  return legend;
}
