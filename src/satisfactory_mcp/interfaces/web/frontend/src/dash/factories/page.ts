/* One factory's page, `dash=factories/<name>[/<aspect>]`: the header with its actions, the
 * overview tiles and tables, or the aspect picked under the header. */

import { adviceCard } from "../../chat/advice";
import { appendNote, button, empty, heading, link, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, mw, pct } from "../../kit/format";
import { startLasso } from "../../map/tools/lasso";
import { startTrace } from "../../map/tools/trace";
import { replaceDash } from "../../app/nav";
import { state } from "../../app/state";
import { vitals } from "../../app/vitals";
import { counted, WORDS } from "../../kit/words";
import { factoryMapButton, leaveDashThen, pointButton, renameButton, requestRender } from "../actions";
import { issueCount, issueGroups, issueTable } from "../machine-health";
import { actionTone, stateTone } from "../machine-states";
import { aspectTabs, factoryAddress, factoryDash, factoryPinButton } from "./address";
import { renderAspect } from "./factory-detail";
import { closeGraph, graphView, openGraph, renderGraphCard, resetGraphOnNewEpoch } from "./graph-view";
import { healthOrPlaceholder } from "./list";
import { resetDetectOnNewEpoch } from "./detect";
import { onRenamed, renamedTo } from "./rename";

import type { FactoryHealthRow } from "../../api/shapes";

function worstList(parent: HTMLElement, row: FactoryHealthRow): void {
  const section = make("section", "dash-card");
  heading(section, WORDS.needAction);
  const found = issueGroups([row]);
  if (!found.length) {
    empty(
      section,
      "none " + WORDS.needAction,
      row.attention ? counted(row.attention, "machine") + " " + WORDS.notRunning : ""
    );
    parent.appendChild(section);
    return;
  }
  section.appendChild(
    issueTable(
      found,
      function (group) {
        return pointButton(group.issues[0]!, "show " + group.what + " on the map");
      },
      false
    )
  );
  const listed = issueCount(found);
  if (listed < row.actionable) appendNote(section, "showing " + count(listed) + " of " + count(row.actionable));
  parent.appendChild(section);
}

function statesTable(parent: HTMLElement, row: FactoryHealthRow): void {
  const section = make("section", "dash-card");
  heading(section, "machines by state");
  let biggest = 1;
  row.states.forEach(function (entry) {
    biggest = Math.max(biggest, entry.count);
  });
  type StateRow = FactoryHealthRow["states"][number];
  section.appendChild(
    table<StateRow>(
      [
        {
          key: "state",
          label: "state",
          render: function (entry) {
            return entry.state;
          },
        },
        {
          key: "machines",
          label: "machines",
          align: "right",
          render: function (entry) {
            return count(entry.count);
          },
        },
        {
          key: "bar",
          label: "",
          className: "bar",
          render: function (entry) {
            const bar = make("div", "dash-hbar");
            const fill = make("span", "dash-mix-" + stateTone(entry.state));
            fill.style.width = (entry.count / biggest) * 100 + "%";
            bar.appendChild(fill);
            return bar;
          },
        },
      ],
      row.states,
      {
        rowClass: function (entry) {
          const tone = stateTone(entry.state);
          return tone === "ok" ? "" : tone;
        },
        caption: "machines by state",
      }
    )
  );
  parent.appendChild(section);
}

function factoryGraphShown(name: string): boolean {
  return graphView.source === "factory" && graphView.subject === name;
}

/* A name renamed since the page was linked: follow it to the new address. */
function followRename(body: HTMLElement, name: string, aspect: string): boolean {
  const now = renamedTo(name);
  if (!now) return false;
  replaceDash(factoryDash(now, aspect));
  renderFactory(body, factoryDash(now, aspect).slice("factories/".length));
  return true;
}

function factoryHeader(row: FactoryHealthRow, aspect: string): HTMLElement {
  const head = make("div", "dash-title");
  const title = make("h1", "", row.name);
  head.appendChild(title);
  head.appendChild(
    renameButton(row.name, title, function (to) {
      if (state.dash === factoryDash(row.name, aspect)) replaceDash(factoryDash(to, aspect));
      requestRender();
    })
  );
  head.appendChild(factoryMapButton(row));
  const shown = factoryGraphShown(row.name);
  const toggle = button(
    shown ? "hide graph" : "graph",
    function () {
      if (shown) closeGraph();
      else openGraph("factory", row.name, row.name);
    },
    { title: "draw this factory's production graph" }
  );
  toggle.setAttribute("data-ctl", "graph");
  toggle.setAttribute("aria-expanded", String(shown));
  head.appendChild(toggle);
  head.appendChild(
    button(
      "trace supply",
      function () {
        leaveDashThen(function () {
          startTrace("label:" + row.name, "up");
        });
      },
      { title: "draw what feeds this factory on the map, with items and rates" }
    )
  );
  head.appendChild(
    button(
      "amend on map",
      function () {
        leaveDashThen(function () {
          startLasso(row.name);
        });
      },
      { title: "draw around machines on the map to add them to this factory or remove them" }
    )
  );
  head.appendChild(factoryPinButton(row.name));
  return head;
}

function factoryTiles(row: FactoryHealthRow): HTMLElement {
  const power = row.unwired + row.no_generator;
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("machines", count(row.machines), pct(row.uptime) + " mean uptime"));
  const need = tile(WORDS.needAction, count(row.actionable), count(row.attention) + " " + WORDS.notRunning);
  const tone = actionTone(row.states);
  if (tone) need.classList.add(tone);
  tiles.appendChild(need);
  tiles.appendChild(tile(WORDS.measuredDraw, mw(row.measured_mw), mw(row.nameplate_mw) + " nameplate"));
  tiles.appendChild(
    tile(
      WORDS.powerProblems,
      count(power),
      power ? count(row.unwired) + " " + WORDS.noWire + " · " + count(row.no_generator) + " " + WORDS.noGenerator : "none",
      power > 0
    )
  );
  return tiles;
}

export function renderFactory(body: HTMLElement, subject: string): void {
  resetGraphOnNewEpoch();
  resetDetectOnNewEpoch();
  const health = vitals().health;
  const at = factoryAddress(subject, function (whole) {
    return !!health && health.factories.some(function (row) {
      return row.name === whole;
    });
  });
  const name = at.name;
  const aspect = at.aspect;
  const row = health
    ? health.factories.filter(function (candidate) {
        return candidate.name === name;
      })[0]
    : undefined;
  if (health && !row && followRename(body, name, aspect)) return;
  body.appendChild(link("factories", "‹ all factories", "dash-back"));
  if (!healthOrPlaceholder(body)) return;
  if (!row) {
    empty(body, "no factory named “" + name + "” in this world", "it may have been renamed or forgotten; pick one from the list");
    return;
  }
  body.appendChild(factoryHeader(row, aspect));
  if (factoryGraphShown(row.name)) renderGraphCard(body);
  body.appendChild(aspectTabs(row.name, aspect));
  if (aspect) {
    renderAspect(body, row.name, aspect);
    return;
  }
  if (row.review) appendNote(body, "label " + row.review + ": " + row.alive + " of " + row.anchors + " anchors still stand");
  body.appendChild(factoryTiles(row));
  adviceCard(body, { factory: row.name });

  const split = make("div", "dash-split");
  statesTable(split, row);
  worstList(split, row);
  body.appendChild(split);
}

onRenamed(function () {
  if (state.dash.indexOf("factories/") === 0) requestRender();
});
