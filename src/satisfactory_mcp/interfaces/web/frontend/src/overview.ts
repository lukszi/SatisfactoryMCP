/* The dashboard's Overview tab: headline tiles, the factories and machines that need action,
 * power problems and power per circuit, addressed as `dash=overview`. */

import { empty, error, heading, issueCount, issueGroups, issueTable, link, loading, note, table, tile } from "./dashkit";
import { make } from "./dom";
import { count, pct, spoken } from "./format";
import { loadOne } from "./load";
import { hashFor } from "./map";
import { vitals } from "./panel";
import { milestoneTile } from "./progress";
import { actionTone, isFine, needsAction, stateSets, statesOf, tone } from "./states";
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

import type { FactoryHealthRow } from "./api-shapes";

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
    false,
    hashFor("factories")
  );
  var shade = actionTone(statesOf(rows));
  if (shade) box.classList.add(shade);
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
          tone: function (r) {
            return actionTone(r.states);
          },
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

function machinesCard(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "machines that " + W.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  var factories = vitals().health!.factories;
  var found = issueGroups(factories);
  if (!found.length) {
    empty(card, "none " + W.needAction);
    return;
  }
  var total = 0;
  factories.forEach(function (r) {
    total += r.actionable;
  });
  var shown = found.slice(0, ROWS_SHOWN);
  card.appendChild(
    issueTable(
      shown,
      function (g) {
        return pointButton(g.issues[0]!, "show " + g.what + " in " + g.factory + " on the map");
      },
      true
    )
  );
  var listed = issueCount(shown);
  if (listed < total) note(card, "showing " + count(listed) + " of " + count(total) + "; Factories lists all");
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
