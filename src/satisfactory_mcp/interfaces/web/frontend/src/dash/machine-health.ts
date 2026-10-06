/* Machines that need action, grouped as the dashboard lists them, and the state mix bar the
 * Overview and the Factories tab both draw. */

import { link, table } from "../kit/dashkit";
import { make } from "../kit/dom";
import { count, spoken } from "../kit/format";
import { vitals } from "../app/vitals";
import { isFine, needsAction, stateSets, tone } from "./machine-states";
import { W } from "../kit/words";

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
  var items = issue.cause.join(", ");
  if (issue.state === W.blocked) return "can't output " + items;
  if (issue.state === "starved") return "short of " + items;
  return items;
}

export function issueGroups(rows: { name: string; worst_actionable: MachineIssue[] }[]): IssueGroup[] {
  var found: IssueGroup[] = [];
  rows.forEach(function (r) {
    r.worst_actionable.forEach(function (issue) {
      var cause = issueCauseText(issue);
      var same = found.filter(function (g) {
        return g.factory === r.name && g.state === issue.state && g.what === issue.what && g.cause === cause;
      })[0];
      if (same) same.issues.push(issue);
      else found.push({ factory: r.name, state: issue.state, what: issue.what, cause: cause, issues: [issue] });
    });
  });
  return found;
}

export function issueCount(groups: IssueGroup[]): number {
  var n = 0;
  groups.forEach(function (g) {
    n += g.issues.length;
  });
  return n;
}

export function issueTable(
  groups: IssueGroup[],
  place: (g: IssueGroup) => HTMLElement,
  withFactory: boolean
): HTMLElement {
  var columns: Column<IssueGroup>[] = [
    {
      key: "state",
      label: "state",
      className: "dash-nowrap",
      tone: function (g) {
        return tone(g.state, true);
      },
      render: function (g) {
        return g.state;
      },
    },
    {
      key: "n",
      label: "machines",
      align: "right",
      render: function (g) {
        return count(g.issues.length);
      },
    },
    {
      key: "what",
      label: "machine",
      className: "dash-wide",
      render: function (g) {
        if (!g.cause) return g.what;
        var cell = make("span", "", g.what);
        cell.appendChild(make("span", "dash-sub", g.cause));
        return cell;
      },
    },
  ];
  if (withFactory) {
    columns.push({
      key: "factory",
      label: W.factory,
      render: function (g) {
        var a = link("factories/" + g.factory, g.factory, "dash-trunc");
        a.title = g.factory;
        return a;
      },
    });
  }
  columns.push({ key: "map", label: "", align: "right", render: place });
  return table<IssueGroup>(columns, groups, {
    rowTitle: function (g) {
      return g.issues
        .map(function (i) {
          return i.instance;
        })
        .join(", ");
    },
    caption: "machines that " + W.needAction,
  });
}

export interface Mix {
  bad: number;
  blocked: number;
  mid: number;
  ok: number;
}

export function actionable(): string[] {
  return stateSets().actionable;
}

function middling(): string[] {
  var h = vitals().health;
  return (h ? h.states : []).filter(function (s) {
    return !needsAction(s) && !isFine(s);
  });
}

export function mixOf(rows: FactoryHealthRow[]): Mix {
  var mix = { bad: 0, blocked: 0, mid: 0, ok: 0 };
  rows.forEach(function (row) {
    row.states.forEach(function (s) {
      mix[tone(s.state)] += s.count;
    });
  });
  return mix;
}

function mixWords(): [keyof Mix, string][] {
  return [
    [
      "bad",
      spoken(
        actionable().filter(function (s) {
          return s !== W.blocked;
        }),
        "or"
      ),
    ],
    ["blocked", W.blocked],
    ["mid", spoken(middling(), "or")],
    ["ok", "running or unmonitored"],
  ];
}

export function mixBar(mix: Mix): HTMLElement {
  var total = mix.bad + mix.blocked + mix.mid + mix.ok;
  var bar = make("div", "dash-mix");
  bar.setAttribute("role", "img");
  var words: string[] = [];
  mixWords().forEach(function (p) {
    var n = mix[p[0]];
    words.push(count(n) + " " + p[1]);
    if (!n || !total) return;
    var seg = make("span", "dash-mix-" + p[0]);
    seg.style.width = (n / total) * 100 + "%";
    bar.appendChild(seg);
  });
  bar.title = words.join(", ");
  bar.setAttribute("aria-label", bar.title);
  return bar;
}

export function mixLegend(mix: Mix): HTMLElement {
  var legend = make("div", "dash-mix-legend");
  legend.setAttribute("aria-hidden", "true");
  mixWords().forEach(function (p) {
    var item = make("span", "dash-key");
    item.appendChild(make("i", "dash-swatch dash-mix-" + p[0]));
    item.appendChild(document.createTextNode(count(mix[p[0]]) + " " + p[1]));
    legend.appendChild(item);
  });
  return legend;
}
