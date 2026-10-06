/* World > pickups: the census per kind, and what is left, what was taken, and what is nearest.
 * See docs/world-finders_contract.md §2.4. */

import { appendNote, button, empty, heading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { showRows } from "../../map/tools/finder";
import { pickupPlace, pickupSelection, worldUrl } from "./world-finds";
import { count } from "../../kit/format";
import { hashFor } from "../../map/map";
import { lootLine, pickupName } from "../../map/drawn/pickups";
import { isSelected } from "../../app/selection";
import { spoilerFlag } from "../../app/settings";
import { state } from "../../app/state";
import { counted, WORDS } from "../../kit/words";
import { mapButton } from "../actions";
import {
  copyCell,
  distanceColumn,
  filterBar,
  goToWorldParams,
  hiddenLine,
  loaded,
  numericColumn,
  openRowsOnMap,
  selectAndRender,
  selectField,
  showAllToggle,
  staleLine,
  viewDash,
  waiting,
  want,
  withParams,
} from "./world-kit";

import type { Column, SortState } from "../../kit/dashkit";
import type { CensusRow, CollectibleRow, CollectiblesResponse } from "../../api/shapes";

const box = loaded<CollectiblesResponse>();

const sorts: Record<string, SortState> = {
  census: { key: "remaining", desc: true },
  remaining: { key: "kind", desc: false },
  collected: { key: "kind", desc: false },
  nearest: { key: "distance", desc: false },
};

const LISTS: [string, string][] = [
  ["remaining", WORDS.remaining],
  ["collected", WORDS.collected],
  ["nearest", "nearest"],
];

function listOf(params: Record<string, string>): string {
  return params.view === "collected" || params.view === "nearest" ? params.view : "remaining";
}

/* An unknown count reads "–" and sorts below zero. */
function censusCount(key: string, label: string, pick: (row: CensusRow) => number | null, title?: string): Column<CensusRow> {
  return numericColumn<CensusRow>(key, label, pick, { title: title, nullsFirst: true });
}

function censusTable(pickups: CollectiblesResponse): HTMLElement {
  const stale = pickups.stale;
  const foreign = !!stale && stale.observed_matches === false;
  const streamedTitle =
    "placed where no save has had them loaded" +
    (stale && stale.observed_from ? "; read from the saves of " + stale.observed_from : "") +
    (foreign ? ", another world, so left out" : "");
  const columns: Column<CensusRow>[] = [
    {
      key: "kind",
      label: "kind",
      sort: function (row) {
        return row.label;
      },
      render: function (row) {
        return row.label;
      },
    },
    censusCount("placed", "placed", function (row) { return row.placed; }),
    censusCount("collected", WORDS.collected, function (row) { return row.collected; }),
    censusCount("remaining", WORDS.remaining, function (row) { return row.remaining; }),
    censusCount("standing", "standing", function (row) { return row.standing; }, "seen still standing in a save that had them loaded"),
    censusCount("streamed", WORDS.neverStreamed, function (row) { return row.never_streamed; }, streamedTitle),
  ];
  return table(columns, pickups.census, { sort: sorts.census, caption: "pickups per kind" });
}

function totals(pickups: CollectiblesResponse): string {
  let placed = 0;
  let collected = 0;
  pickups.census.forEach(function (row) {
    placed += row.placed;
    collected += row.collected;
  });
  return count(collected) + " " + WORDS.collected + " of " + count(placed) + " placed";
}

function stateText(pickup: CollectibleRow): string {
  if (pickup.collected) return WORDS.collected;
  return pickup.observed ? pickup.observed.replace(/_/g, " ") : "unknown";
}

function listTable(rows: CollectibleRow[], list: string): HTMLElement {
  const from = state.dash;
  const columns: Column<CollectibleRow>[] = [
    {
      key: "kind",
      label: "kind",
      sort: function (pickup) {
        return pickupName(pickup.category);
      },
      render: function (pickup) {
        return pickupName(pickup.category);
      },
    },
    {
      key: "state",
      label: "state",
      render: function (pickup) {
        const loot = lootLine(pickup);
        const cell = make("span", "", stateText(pickup));
        if (loot) cell.appendChild(make("span", "dash-sub", loot));
        return cell;
      },
    },
  ];
  if (list === "nearest") columns.push(distanceColumn<CollectibleRow>());
  columns.push({
    key: "at",
    label: "selector",
    className: "dash-nowrap",
    title: "the place, as every near= takes it",
    render: function (pickup) {
      return copyCell(pickupPlace(pickup));
    },
  });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (pickup) {
      return mapButton("fly the map to it, ring it and show its layer", function () {
        showRows({ kind: "pickups", rows: [pickup] }, pickupName(pickup.category), from, 0);
      }, "show this " + pickupName(pickup.category) + " pickup on the map");
    },
  });
  return table(columns, rows, {
    sort: sorts[list],
    caption: list + " pickups",
    onRow: selectAndRender(pickupSelection),
    rowClass: function (pickup) {
      return isSelected("pickup", pickup.name) ? "on" : "";
    },
  });
}

function groupOptions(pickups: CollectiblesResponse): [string, string][] {
  return [["", "every kind"] as [string, string]].concat(
    pickups.census.map(function (row): [string, string] {
      return [row.category, row.label];
    })
  );
}

export function renderPickups(body: HTMLElement, params: Record<string, string>): void {
  const list = listOf(params);
  const top = make("section", "dash-card");
  body.appendChild(top);
  heading(top, "pickups per kind");
  want(
    "world-pickups",
    box,
    worldUrl("/api/collectibles", {
      mode: list,
      group: params.group || "",
      near: list === "nearest" ? params.near || "me" : "",
      spoilers: spoilerFlag(),
    })
  );
  if (waiting(top, box, "pickups")) return;
  const pickups = box.data!;
  top.appendChild(make("div", "world-census", totals(pickups)));
  hiddenLine(top, pickups.hidden_spoilers, "kind not found yet", "kinds not found yet");
  if (pickups.census.length) top.appendChild(censusTable(pickups));
  staleLine(top, pickups.stale);
  const card = make("section", "dash-card");
  body.appendChild(card);
  card.appendChild(
    subTabs(
      LISTS.map(function (entry) {
        return { id: entry[0], label: entry[1], href: hashFor(viewDash("pickups", withParams(params, { view: entry[0] === "remaining" ? "" : entry[0] }))) };
      }),
      list,
      undefined,
      "which pickups to list"
    )
  );
  const bar = filterBar(card);
  bar.appendChild(
    selectField("kind", "world-pickups-group", params.group || "", groupOptions(pickups), function (value) {
      goToWorldParams(withParams(params, { group: value }));
    })
  );
  const rows = pickups.rows;
  if (!rows.length) {
    empty(card, list === "collected" ? "nothing collected yet" : "no pickup left" + (params.group ? " of this kind" : ""));
    return;
  }
  const line = make("div", "world-census");
  line.appendChild(make("span", "", counted(rows.length, "pickup") + (pickups.where ? " · nearest to " + pickups.where : "")));
  const paired = params.group
    ? []
    : pickups.census.filter(function (row) {
        return !!row.pedestal_of;
      });
  line.appendChild(
    button("show all on map", function () {
      openRowsOnMap({ kind: "pickups", rows: rows }, list + " pickups");
    }, { map: true, title: "ring every row on the map and list them beside it" })
  );
  card.appendChild(line);
  if (paired.length) {
    appendNote(
      card,
      paired
        .map(function (row) {
          return row.label;
        })
        .join(", ") + " are listed with what stands on them, so this list is shorter than the kinds table adds up to"
    );
  }
  const grid = listTable(rows, list);
  card.appendChild(grid);
  showAllToggle(card, grid, rows.length, "pickups-" + list, "pickup");
}
