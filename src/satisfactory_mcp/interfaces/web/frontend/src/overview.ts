/* The dashboard's Overview tab: headline tiles, the factories and machines that need action,
 * power problems and power per circuit, addressed as `dash=overview`. */

import { empty, error, heading, link, loading, note, table, tile } from "./dashkit";
import { make } from "./dom";
import { count, pct, spoken } from "./format";
import { loadOne } from "./load";
import { hashFor } from "./map";
import { vitals } from "./panel";
import { milestoneTile } from "./progress";
import { isFine, needsAction, stateSets, tone } from "./states";
import { factoryMapButton, go, pointButton } from "./dashboard";
import {
  circuitTable,
  faultCount,
  faultsOf,
  faultWords,
  generationTile,
  headroomTiles,
  problemTable,
  retryCircuits,
  world,
} from "./power-tab";
import { bar as powerBar } from "./powerview";
import { counted, W } from "./words";

import type { FactoryHealthRow, MachineIssue } from "./api-shapes";

var ROWS_SHOWN = 12;

interface Mix {
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

function mixLegend(mix: Mix): HTMLElement {
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

function retryHealth(): void {
  loadOne("/api/factories/health");
}

function detectLink(): HTMLElement {
  return link("factories", "detect factories on the Factories tab");
}

function actionTile(rows: FactoryHealthRow[]): HTMLElement {
  var v = vitals();
  if (!v.health) return tile(W.needAction, "–", v.healthError ? "factory health could not be read" : "loading…");
  if (!rows.length) {
    var none = tile(W.needAction, "–", "no factories named yet", false);
    none.appendChild(detectLink());
    return none;
  }
  var mix = mixOf(rows);
  var todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  var box = tile(
    W.needAction,
    count(mix.bad + mix.blocked),
    "machines, in " + count(todo) + " of " + counted(rows.length, "named factory", "named factories"),
    mix.bad > 0,
    hashFor("factories")
  );
  if (!mix.bad && mix.blocked) box.classList.add("blocked");
  box.appendChild(mixBar(mix));
  box.appendChild(mixLegend(mix));
  return box;
}

function tiles(body: HTMLElement): void {
  var v = vitals();
  var row = make("div", "dash-tiles");
  var rows = v.health ? v.health.factories : [];
  row.appendChild(actionTile(rows));
  var power = hashFor("power");
  if (v.circuits) {
    var gens = 0;
    v.circuits.generators.forEach(function (g) {
      gens += g.count;
    });
    var gen = generationTile(world(v.circuits), counted(gens, "generator"), power);
    gen.appendChild(powerBar(v.circuits.world));
    row.appendChild(gen);
    headroomTiles(world(v.circuits), power).forEach(function (t) {
      row.appendChild(t);
    });
    var faults = faultsOf(v.circuits);
    row.appendChild(tile(W.powerProblems, count(faultCount(faults)), faultWords(faults), faultCount(faults) > 0, power));
  } else {
    var why = v.circuitsError ? "power circuits could not be read" : "loading…";
    row.appendChild(tile(W.generation, "–", why));
    row.appendChild(tile(W.powerProblems, "–", why));
  }
  row.appendChild(milestoneTile());
  body.appendChild(row);
}

function healthMissing(parent: HTMLElement): boolean {
  var v = vitals();
  if (v.health) return false;
  if (v.healthError) error(parent, "factory health", "", retryHealth);
  else loading(parent, "factory health");
  return true;
}

function factoriesCard(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "factories that " + W.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  var rows = vitals().health!.factories;
  if (!rows.length) {
    empty(card, "no factories named yet", detectLink());
    return;
  }
  var worst = rows.filter(function (r) {
    return r.actionable > 0;
  });
  if (!worst.length) {
    empty(card, "none " + W.needAction);
    return;
  }
  card.appendChild(
    table<FactoryHealthRow>(
      [
        {
          key: "name",
          label: W.factory,
          render: function (r) {
            var a = link("factories/" + r.name, r.name, "dash-trunc");
            a.title = r.name;
            return a;
          },
        },
        {
          key: "actionable",
          label: W.needAction,
          align: "right",
          className: "bad",
          render: function (r) {
            return count(r.actionable);
          },
        },
        {
          key: "uptime",
          label: "uptime",
          align: "right",
          render: function (r) {
            return pct(r.uptime);
          },
        },
        { key: "map", label: "", align: "right", render: factoryMapButton },
      ],
      worst,
      {
        onRow: function (r) {
          go("factories/" + r.name);
        },
        caption: "factories that " + W.needAction,
      }
    )
  );
}

interface Group {
  factory: string;
  state: string;
  what: string;
  cause: string;
  issues: MachineIssue[];
}

function because(issue: MachineIssue): string {
  if (!issue.cause.length) return "";
  var items = issue.cause.join(", ");
  if (issue.state === W.blocked) return "can't output " + items;
  if (issue.state === "starved") return "short of " + items;
  return items;
}

function groups(): { rows: Group[]; listed: number; total: number } {
  var found: Group[] = [];
  var listed = 0;
  var total = 0;
  vitals().health!.factories.forEach(function (r) {
    total += r.actionable;
    r.worst_actionable.forEach(function (issue) {
      listed += 1;
      var cause = because(issue);
      var same = found.filter(function (g) {
        return g.factory === r.name && g.state === issue.state && g.what === issue.what && g.cause === cause;
      })[0];
      if (same) same.issues.push(issue);
      else found.push({ factory: r.name, state: issue.state, what: issue.what, cause: cause, issues: [issue] });
    });
  });
  return { rows: found, listed: listed, total: total };
}

function machinesCard(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "machines that " + W.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  var found = groups();
  if (!found.rows.length) {
    empty(card, "none " + W.needAction);
    return;
  }
  var shown = found.rows.slice(0, ROWS_SHOWN);
  card.appendChild(
    table<Group>(
      [
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
        {
          key: "factory",
          label: W.factory,
          render: function (g) {
            var a = link("factories/" + g.factory, g.factory, "dash-trunc");
            a.title = g.factory;
            return a;
          },
        },
        {
          key: "map",
          label: "",
          align: "right",
          render: function (g) {
            return pointButton(g.issues[0]!, "show " + g.what + " in " + g.factory + " on the map");
          },
        },
      ],
      shown,
      {
        rowTitle: function (g) {
          return g.issues
            .map(function (i) {
              return i.instance;
            })
            .join(", ");
        },
        caption: "machines that " + W.needAction,
      }
    )
  );
  var listed = 0;
  shown.forEach(function (g) {
    listed += g.issues.length;
  });
  if (listed < found.total) note(card, "showing " + count(listed) + " of " + count(found.total) + "; Factories lists all");
}

function problemsCard(parent: HTMLElement): void {
  var v = vitals();
  var card = make("section", "dash-card");
  heading(card, W.powerProblems);
  parent.appendChild(card);
  if (!v.circuits) {
    if (v.circuitsError) error(card, "power circuits", "", retryCircuits);
    else loading(card, "power circuits");
    return;
  }
  var faults = faultsOf(v.circuits);
  if (!faultCount(faults)) {
    empty(card, "no " + W.powerProblems);
    return;
  }
  problemTable(card, faults, ROWS_SHOWN);
}

function circuitsCard(parent: HTMLElement): void {
  var v = vitals();
  var card = make("section", "dash-card");
  heading(card, "power per circuit");
  parent.appendChild(card);
  if (v.circuits) circuitTable(card, v.circuits.circuits);
  else if (v.circuitsError) error(card, "power circuits", "", retryCircuits);
  else loading(card, "power circuits");
}

export function renderOverview(body: HTMLElement): void {
  tiles(body);
  var split = make("div", "dash-split");
  var h = vitals().health;
  factoriesCard(split);
  if (!h || h.factories.length) machinesCard(split);
  body.appendChild(split);
  problemsCard(body);
  circuitsCard(body);
}
