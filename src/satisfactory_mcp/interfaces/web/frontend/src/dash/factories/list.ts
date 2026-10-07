/* The Factories tab, `dash=factories`: the unnamed-cluster card over the table of named
 * factories. */

import { empty, error, heading, link, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, joinWithConjunction, mw, pct } from "../../kit/format";
import { loadOne } from "../../app/load";
import { go } from "../../app/nav";
import { vitals } from "../../app/vitals";
import { counted, WORDS } from "../../kit/words";
import { factoryMapButton, renameButton, requestRender } from "../actions";
import { mixBar, mixOf } from "../machine-health";
import { actionTone, stateSets } from "../machine-states";
import { resetDetectOnNewEpoch, renderDetect } from "./detect";
import { resetGraphOnNewEpoch } from "./graph-view";

import type { Column, SortState } from "../../kit/dashkit";
import type { FactoryHealthResponse, FactoryHealthRow } from "../../api/shapes";

const factorySort: SortState = { key: "actionable", desc: true };

function sortValue(row: FactoryHealthRow, key: keyof FactoryHealthRow): number | string {
  if (key === "name") return row.name.toLowerCase();
  if (key === "uptime") return row.uptime === null ? -1 : row.uptime;
  const value: unknown = row[key];
  return typeof value === "number" ? value : 0;
}

function counterColumn(key: keyof FactoryHealthRow, label: string, flag: boolean, title: string): Column<FactoryHealthRow> {
  return {
    key: key,
    label: label,
    align: "right",
    title: title,
    sort: function (row) {
      return sortValue(row, key);
    },
    tone: flag
      ? function (row) {
          return row[key] ? "bad" : "";
        }
      : undefined,
    render: function (row) {
      return count(row[key] as number);
    },
  };
}

function needColumn(): Column<FactoryHealthRow> {
  const column = counterColumn("actionable", WORDS.needAction, false, WORDS.needAction + ": " + joinWithConjunction(stateSets().actionable, "or"));
  column.tone = function (row) {
    return actionTone(row.states);
  };
  return column;
}

function factoryColumns(): Column<FactoryHealthRow>[] {
  return [
    {
      key: "name",
      label: "factory",
      sort: function (row) {
        return sortValue(row, "name");
      },
      render: function (row) {
        const named = make("span", "dash-named");
        named.appendChild(link("factories/" + row.name, row.name));
        named.appendChild(
          renameButton(row.name, named, function () {
            requestRender();
          })
        );
        return named;
      },
    },
    {
      key: "alive",
      label: "machines",
      align: "right",
      title: "machines standing, of the anchors the label holds",
      sort: function (row) {
        return sortValue(row, "alive");
      },
      render: function (row) {
        return count(row.alive) + (row.alive === row.anchors ? "" : " of " + count(row.anchors));
      },
    },
    {
      key: "uptime",
      label: "uptime",
      align: "right",
      title: "each machine's last 300 s, averaged; a blocked machine makes nothing, so a blocked factory reads near 0%",
      sort: function (row) {
        return sortValue(row, "uptime");
      },
      render: function (row) {
        return pct(row.uptime);
      },
    },
    needColumn(),
    counterColumn("attention", WORDS.notRunning, false, "every machine that is not " + joinWithConjunction(stateSets().ok, "or")),
    counterColumn("unwired", WORDS.noWire, true, WORDS.powerProblems + ": machines with " + WORDS.noWire),
    counterColumn("no_generator", WORDS.noGenerator, true, WORDS.powerProblems + ": machines on a circuit with " + WORDS.noGenerator),
    {
      key: "measured_mw",
      label: WORDS.measuredDraw,
      align: "right",
      sort: function (row) {
        return sortValue(row, "measured_mw");
      },
      render: function (row) {
        return mw(row.measured_mw);
      },
    },
    {
      key: "nameplate_mw",
      label: WORDS.nameplateDraw,
      align: "right",
      sort: function (row) {
        return sortValue(row, "nameplate_mw");
      },
      render: function (row) {
        return mw(row.nameplate_mw);
      },
    },
    {
      key: "mix",
      label: "state mix",
      className: "mix",
      render: function (row) {
        return mixBar(mixOf([row]));
      },
    },
    { key: "map", label: "", align: "right", render: factoryMapButton },
  ];
}

/* The health read, or a loading or error line in its place. */
export function healthOrPlaceholder(body: HTMLElement): FactoryHealthResponse | null {
  const v = vitals();
  if (v.health) return v.health;
  if (v.healthError) {
    error(body, "factory health", v.healthError, function () {
      loadOne("/api/factories/health");
    });
  } else loading(body, "factory health");
  return null;
}

export function renderFactories(body: HTMLElement): void {
  resetGraphOnNewEpoch();
  resetDetectOnNewEpoch();
  const health = healthOrPlaceholder(body);
  if (!health) return;
  renderDetect(body);
  const rows = health.factories.slice();
  if (!rows.length) {
    empty(body, "no factories named yet", "detect them above, or ask chat to name one");
    return;
  }
  heading(body, counted(rows.length, "named factory", "named factories"));
  body.appendChild(
    table<FactoryHealthRow>(factoryColumns(), rows, {
      sort: factorySort,
      onSort: requestRender,
      onRow: function (row) {
        go("factories/" + row.name);
      },
      caption: "named factories",
    })
  );
}
