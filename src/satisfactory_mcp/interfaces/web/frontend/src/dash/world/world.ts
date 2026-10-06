/* The dashboard's World section: where the player is, and the finders for nodes, fields,
 * conduits, pickups and regions. See docs/world-finders_contract.md §2 and §7. */

import { appendNote, empty, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, formatNumber } from "../../kit/format";
import { hashFor, writeHash } from "../../map/map";
import { go, subjectQuery } from "../../app/nav";
import { showPoint } from "../../map/map-highlight";
import { onSetting, settingChoice } from "../../app/settings";
import { state } from "../../app/state";
import { notify, offer } from "../../kit/toast";
import { actorWord } from "../planner/planner-core";
import { mapButton } from "../actions";
import { resourceOptions, worldUrl } from "./world-finds";
import { renderConduits } from "./world-conduits";
import { renderHere } from "./world-here";
import { renderNodes } from "./world-nodes";
import { renderPickups } from "./world-pickups";
import { fromFieldsRank, renderRank } from "./world-rank";
import { address, copyCell, filterBar, goToWorldParams, loaded, redraw, selectField, viewDash, VIEWS, waiting, want } from "./world-kit";

import type { Column, SortState } from "../../kit/dashkit";
import type { RegionRow, RegionTableResponse } from "../../api/shapes";
import type { ActivityEvent } from "../planner/planner-core";

const regionsBox = loaded<RegionTableResponse>();

const regionSort: SortState = { key: "nodes", desc: true };

function regionColumns(): Column<RegionRow>[] {
  return [
    { key: "name", label: "region", sort: function (region) { return region.name; }, render: function (region) { return region.name; } },
    { key: "direction", label: "direction", render: function (region) { return region.direction; } },
    { key: "grid", label: "grid", sort: function (region) { return region.grid; }, render: function (region) { return region.grid; } },
    {
      key: "area",
      label: "area",
      align: "right",
      sort: function (region) { return region.area_km2; },
      render: function (region) { return formatNumber(region.area_km2, 2) + " km²"; },
    },
    { key: "nodes", label: "nodes", align: "right", sort: function (region) { return region.nodes; }, render: function (region) { return count(region.nodes); } },
    { key: "selector", label: "selector", render: function (region) { return copyCell("region:" + region.name); } },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (region) {
        return mapButton("fly the map to the region's name", function () {
          showPoint(region.anchor_m[0], region.anchor_m[1], { label: region.name });
        }, "show " + region.name + " on the map");
      },
    },
  ];
}

function renderRegions(body: HTMLElement, params: Record<string, string>): void {
  const card = make("section", "dash-card");
  body.appendChild(card);
  const bar = filterBar(card);
  bar.appendChild(
    selectField("resource", "world-regions-resource", params.resource || "", resourceOptions("every resource", params.resource || ""), function (value) {
      goToWorldParams({ resource: value });
    })
  );
  want("world-regions", regionsBox, worldUrl("/api/world/regions", { resource: params.resource || "" }));
  if (waiting(card, regionsBox, "regions")) return;
  const regions = regionsBox.data!;
  appendNote(card, "region names are good to about " + formatNumber(regions.accuracy_m, 0) + " m" + (regions.resource_name ? " · nodes counted: " + regions.resource_name : ""));
  if (!regions.rows.length) {
    empty(card, "no region holds a matching node");
    return;
  }
  card.appendChild(table(regionColumns(), regions.rows, { sort: regionSort, caption: "regions" }));
}

export function worldTitle(subject: string): string {
  const head = subjectQuery(subject).head;
  let label = "";
  VIEWS.forEach(function (entry) {
    if (entry[0] && entry[0] === head) label = entry[1].charAt(0).toUpperCase() + entry[1].slice(1);
  });
  return label;
}

/* The filters a view tab keeps when another view is picked. */
function carried(view: string, params: Record<string, string>): Record<string, string> {
  const kept: Record<string, string> = {};
  if (view === "nodes" || view === "fields" || view === "rank" || view === "regions") kept.resource = params.resource || "";
  if (view === "nodes" || view === "fields") kept.near = params.near || "";
  return kept;
}

export function renderWorld(body: HTMLElement): void {
  let at = address();
  if (at.view === "fields" && at.params.rank === "1") {
    at = { view: "rank", params: fromFieldsRank(at.params) };
    state.dash = viewDash(at.view, at.params);
    writeHash();
  }
  const params = at.params;
  body.appendChild(
    subTabs(
      VIEWS.map(function (entry) {
        return { id: entry[0], label: entry[1], href: hashFor(viewDash(entry[0], carried(entry[0], params))) };
      }),
      at.view,
      undefined,
      "world view"
    )
  );
  if (at.view === "") renderHere(body);
  else if (at.view === "nodes" || at.view === "fields") renderNodes(body, at.view, at.params);
  else if (at.view === "rank") renderRank(body, at.params);
  else if (at.view === "conduits") renderConduits(body, at.params);
  else if (at.view === "pickups") renderPickups(body, at.params);
  else renderRegions(body, at.params);
}

onSetting(redraw);

function typingInField(): boolean {
  const active = document.activeElement;
  return !!active && /^(INPUT|TEXTAREA)$/.test(active.tagName);
}

/* A find chat ran: follow it, or offer to, as the follow setting says; never mid-typing. */
export function onFindActivity(entry: ActivityEvent): void {
  if (entry.kind !== "world.find" || entry.world !== state.world || entry.actor.kind === "page") return;
  const mode = settingChoice("follow");
  const args = (entry.args || {}) as { view?: string; params?: Record<string, string> };
  if (mode === "off" || typeof args.view !== "string") return;
  const there = viewDash(args.view, args.params || {});
  if (state.dash === there) return;
  const who = actorWord(entry.actor);
  if (mode === "toasts" || typingInField()) {
    offer(who + " " + entry.text, "open", function () {
      go(there);
    });
    return;
  }
  notify(who + " " + entry.text + " (Settings, follow chat)");
  go(there);
}
