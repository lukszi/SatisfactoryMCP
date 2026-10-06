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

const ROWS_SHOWN = 12;

function retryHealth(): void {
  loadOne("/api/factories/health");
}

function detectLink(): HTMLElement {
  return link("factories", "detect factories on the Factories tab");
}

function actionTile(rows: FactoryHealthRow[]): HTMLElement {
  const v = vitals();
  if (!v.health) return tile(WORDS.needAction, "–", v.healthError ? "factory health could not be read" : "loading…");
  if (!rows.length) {
    const none = tile(WORDS.needAction, "–", "no factories named yet", false);
    none.appendChild(detectLink());
    return none;
  }
  const mix = mixOf(rows);
  const needing = rows.filter(function (row) {
    return row.actionable > 0;
  }).length;
  const box = tile(
    WORDS.needAction,
    count(mix.bad + mix.blocked),
    "machines, in " + count(needing) + " of " + counted(rows.length, "named factory", "named factories"),
    false,
    hashFor("factories")
  );
  const tone = actionTone(statesOf(rows));
  if (tone) box.classList.add(tone);
  box.appendChild(mixBar(mix));
  box.appendChild(mixLegend(mix));
  return box;
}

function headlineTiles(body: HTMLElement): void {
  const v = vitals();
  const row = make("div", "dash-tiles");
  const rows = v.health ? v.health.factories : [];
  row.appendChild(actionTile(rows));
  const power = hashFor("power");
  if (v.circuits) {
    let generators = 0;
    v.circuits.generators.forEach(function (group) {
      generators += group.count;
    });
    const generation = generationTile(ratedWorld(v.circuits), counted(generators, "generator"), v.circuits.world.starved_generation_mw > 0, power);
    generation.appendChild(ledgerBar(v.circuits.world));
    row.appendChild(generation);
    headroomTiles(ratedWorld(v.circuits), { href: power }).forEach(function (headroomTile) {
      row.appendChild(headroomTile);
    });
    const faults = faultsOf(v.circuits);
    row.appendChild(tile(WORDS.powerProblems, count(faultCount(faults)), faultWords(faults), faultCount(faults) > 0, power));
  } else {
    const why = v.circuitsError ? "power circuits could not be read" : "loading…";
    row.appendChild(tile(WORDS.generation, "–", why));
    row.appendChild(tile(WORDS.powerProblems, "–", why));
  }
  row.appendChild(milestoneTile());
  body.appendChild(row);
}

/* True when health is not read yet; the loading or error line is drawn instead. */
function healthMissing(parent: HTMLElement): boolean {
  const v = vitals();
  if (v.health) return false;
  if (v.healthError) error(parent, "factory health", "", retryHealth);
  else loading(parent, "factory health");
  return true;
}

function factoriesCard(parent: HTMLElement): void {
  const card = make("section", "dash-card");
  heading(card, "factories that " + WORDS.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  const rows = vitals().health!.factories;
  if (!rows.length) {
    empty(card, "no factories named yet", detectLink());
    return;
  }
  const worst = rows.filter(function (row) {
    return row.actionable > 0;
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
          render: function (row) {
            const anchor = link("factories/" + row.name, row.name, "dash-trunc");
            anchor.title = row.name;
            return anchor;
          },
        },
        {
          key: "actionable",
          label: WORDS.needAction,
          align: "right",
          tone: function (row) {
            return actionTone(row.states);
          },
          render: function (row) {
            return count(row.actionable);
          },
        },
        {
          key: "uptime",
          label: "uptime",
          align: "right",
          render: function (row) {
            return pct(row.uptime);
          },
        },
        { key: "map", label: "", align: "right", render: factoryMapButton },
      ],
      worst,
      {
        onRow: function (row) {
          go("factories/" + row.name);
        },
        caption: "factories that " + WORDS.needAction,
      }
    )
  );
}

function machinesCard(parent: HTMLElement): void {
  const card = make("section", "dash-card");
  heading(card, "machines that " + WORDS.needAction);
  parent.appendChild(card);
  if (healthMissing(card)) return;
  const factories = vitals().health!.factories;
  const found = issueGroups(factories);
  if (!found.length) {
    empty(card, "none " + WORDS.needAction);
    return;
  }
  let total = 0;
  factories.forEach(function (row) {
    total += row.actionable;
  });
  const shown = found.slice(0, ROWS_SHOWN);
  card.appendChild(
    issueTable(
      shown,
      function (group) {
        return pointButton(group.issues[0]!, "show " + group.what + " in " + group.factory + " on the map");
      },
      true
    )
  );
  const listed = issueCount(shown);
  if (listed < total) appendNote(card, "showing " + count(listed) + " of " + count(total) + "; Factories lists all");
}

function problemsCard(parent: HTMLElement): void {
  const v = vitals();
  const card = make("section", "dash-card");
  heading(card, WORDS.powerProblems);
  parent.appendChild(card);
  if (!v.circuits) {
    if (v.circuitsError) error(card, "power circuits", "", retryCircuits);
    else loading(card, "power circuits");
    return;
  }
  const faults = faultsOf(v.circuits);
  if (!faultCount(faults)) {
    empty(card, "no " + WORDS.powerProblems);
    return;
  }
  problemTable(card, faults, ROWS_SHOWN);
}

function circuitsCard(parent: HTMLElement): void {
  const v = vitals();
  const card = make("section", "dash-card");
  heading(card, "power per circuit");
  parent.appendChild(card);
  if (v.circuits) circuitTable(card, v.circuits.circuits);
  else if (v.circuitsError) error(card, "power circuits", "", retryCircuits);
  else loading(card, "power circuits");
}

export function renderOverview(body: HTMLElement): void {
  headlineTiles(body);
  adviceCard(body);
  const split = make("div", "dash-split");
  const health = vitals().health;
  factoriesCard(split);
  if (!health || health.factories.length) machinesCard(split);
  body.appendChild(split);
  problemsCard(body);
  circuitsCard(body);
}
