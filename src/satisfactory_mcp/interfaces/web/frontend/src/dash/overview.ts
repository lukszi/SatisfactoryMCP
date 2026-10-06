/* The dashboard's Overview tab: headline tiles, the factories and machines that need action,
 * power problems and power per circuit, addressed as `dash=overview`. */

import { adviceCard } from "../chat/advice";
import { appendNote, empty, error, heading, link, loading, table, tile } from "../kit/dashkit";
import { make } from "../kit/dom";
import { count, pct } from "../kit/format";
import { loadOne } from "../app/load";
import { hashFor } from "../map/map";
import { vitals } from "../app/vitals";
import { milestoneTile } from "./progress/milestones";
import { actionTone, statesOf } from "./machine-states";
import { issueCount, issueGroups, issueTable, mixBar, mixLegend, mixOf } from "./machine-health";
import { factoryMapButton, pointButton } from "./actions";
import { go } from "../app/nav";
import {
  circuitTable,
  faultCount,
  faultsOf,
  faultWords,
  generationTile,
  headroomTiles,
  problemTable,
  retryCircuits,
} from "./power-tab";
import { ledgerBar, ratedWorld } from "./power-ledger";
import { counted, WORDS } from "../kit/words";

import type { FactoryHealthRow } from "../api/shapes";

var ROWS_SHOWN = 12;

function retryHealth(): void {
  loadOne("/api/factories/health");
}

function detectLink(): HTMLElement {
  return link("factories", "detect factories on the Factories tab");
}

function actionTile(rows: FactoryHealthRow[]): HTMLElement {
  var v = vitals();
  if (!v.health) return tile(WORDS.needAction, "–", v.healthError ? "factory health could not be read" : "loading…");
  if (!rows.length) {
    var none = tile(WORDS.needAction, "–", "no factories named yet", false);
    none.appendChild(detectLink());
    return none;
  }
  var mix = mixOf(rows);
  var todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  var box = tile(
    WORDS.needAction,
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
    var gen = generationTile(ratedWorld(v.circuits), counted(gens, "generator"), v.circuits.world.starved_generation_mw > 0, power);
    gen.appendChild(ledgerBar(v.circuits.world));
    row.appendChild(gen);
    headroomTiles(ratedWorld(v.circuits), { href: power }).forEach(function (t) {
      row.appendChild(t);
    });
    var faults = faultsOf(v.circuits);
    row.appendChild(tile(WORDS.powerProblems, count(faultCount(faults)), faultWords(faults), faultCount(faults) > 0, power));
  } else {
    var why = v.circuitsError ? "power circuits could not be read" : "loading…";
    row.appendChild(tile(WORDS.generation, "–", why));
    row.appendChild(tile(WORDS.powerProblems, "–", why));
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
  heading(card, "factories that " + WORDS.needAction);
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
    empty(card, "none " + WORDS.needAction);
    return;
  }
  card.appendChild(
    table<FactoryHealthRow>(
      [
        {
          key: "name",
          label: WORDS.factory,
          render: function (r) {
            var a = link("factories/" + r.name, r.name, "dash-trunc");
            a.title = r.name;
            return a;
          },
        },
        {
          key: "actionable",
          label: WORDS.needAction,
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
        caption: "factories that " + WORDS.needAction,
      }
    )
  );
}

function machinesCard(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "machines that " + WORDS.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  var factories = vitals().health!.factories;
  var found = issueGroups(factories);
  if (!found.length) {
    empty(card, "none " + WORDS.needAction);
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
  if (listed < total) appendNote(card, "showing " + count(listed) + " of " + count(total) + "; Factories lists all");
}

function problemsCard(parent: HTMLElement): void {
  var v = vitals();
  var card = make("section", "dash-card");
  heading(card, WORDS.powerProblems);
  parent.appendChild(card);
  if (!v.circuits) {
    if (v.circuitsError) error(card, "power circuits", "", retryCircuits);
    else loading(card, "power circuits");
    return;
  }
  var faults = faultsOf(v.circuits);
  if (!faultCount(faults)) {
    empty(card, "no " + WORDS.powerProblems);
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
  adviceCard(body);
  var split = make("div", "dash-split");
  var h = vitals().health;
  factoriesCard(split);
  if (!h || h.factories.length) machinesCard(split);
  body.appendChild(split);
  problemsCard(body);
  circuitsCard(body);
}
