/* World > pickups: the census per kind, and what is left, what was taken, and what is nearest.
 * See docs/world-finders_contract.md §2.4. */

import { button, empty, heading, note, table, tabs2 } from "./dashkit";
import { mapButton, render, toMap } from "./dashboard";
import { make } from "./dom";
import { pickupPlace, pickupSelection, showRows, worldUrl } from "./finder";
import { count } from "./format";
import { hashFor } from "./map";
import { lootLine, pickupName } from "./markers";
import { isSelected, select } from "./selection";
import { spoilerFlag } from "./settings";
import { state } from "./state";
import {
  capped,
  changed,
  copyCell,
  distanceColumn,
  edit,
  filterBar,
  hiddenLine,
  loaded,
  selectField,
  staleLine,
  viewDash,
  waiting,
  want,
} from "./world";
import { counted, W } from "./words";

import type { Column, SortState } from "./dashkit";
import type { CensusRow, CollectibleRow, CollectiblesResponse } from "./api-shapes";

var box = loaded<CollectiblesResponse>();

var sorts: Record<string, SortState> = {
  census: { key: "remaining", desc: true },
  remaining: { key: "kind", desc: false },
  collected: { key: "kind", desc: false },
  nearest: { key: "distance", desc: false },
};

var LISTS: [string, string][] = [
  ["remaining", W.remaining],
  ["collected", W.collected],
  ["nearest", "nearest"],
];

function listOf(params: Record<string, string>): string {
  return params.view === "collected" || params.view === "nearest" ? params.view : "remaining";
}

function censusTable(d: CollectiblesResponse): HTMLElement {
  var stale = d.stale;
  var foreign = !!stale && stale.observed_matches === false;
  function n(key: string, label: string, pick: (c: CensusRow) => number | null, title?: string, className?: string): Column<CensusRow> {
    return {
      key: key,
      label: label,
      align: "right",
      title: title,
      className: className,
      sort: function (c) {
        var v = pick(c);
        return v === null ? -1 : v;
      },
      render: function (c) {
        var v = pick(c);
        return v === null ? "–" : count(v);
      },
    };
  }
  var streamedTitle =
    "placed where no save has had them loaded" +
    (stale && stale.observed_from ? "; read from the saves of " + stale.observed_from : "") +
    (foreign ? ", another world, so it may not hold here" : "");
  var columns: Column<CensusRow>[] = [
    {
      key: "kind",
      label: "kind",
      sort: function (c) {
        return c.label;
      },
      render: function (c) {
        return c.label;
      },
    },
    n("placed", "placed", function (c) { return c.placed; }),
    n("collected", W.collected, function (c) { return c.collected; }),
    n("remaining", W.remaining, function (c) { return c.remaining; }),
    n("standing", "standing", function (c) { return c.standing; }, "seen still standing in a save that had them loaded"),
    n("streamed", W.neverStreamed, function (c) { return c.never_streamed; }, streamedTitle, foreign ? "dash-muted" : undefined),
  ];
  return table(columns, d.census, { sort: sorts.census, caption: "pickups per kind" });
}

function totals(d: CollectiblesResponse): string {
  var placed = 0;
  var collected = 0;
  d.census.forEach(function (c) {
    placed += c.placed;
    collected += c.collected;
  });
  return count(collected) + " " + W.collected + " of " + count(placed) + " placed";
}

function stateText(r: CollectibleRow): string {
  if (r.collected) return W.collected;
  return r.observed ? r.observed.replace(/_/g, " ") : "unknown";
}

function listTable(rows: CollectibleRow[], list: string): HTMLElement {
  var from = state.dash;
  var columns: Column<CollectibleRow>[] = [
    {
      key: "kind",
      label: "kind",
      sort: function (r) {
        return pickupName(r.category);
      },
      render: function (r) {
        return pickupName(r.category);
      },
    },
    {
      key: "state",
      label: "state",
      render: function (r) {
        var loot = lootLine(r);
        var cell = make("span", "", stateText(r));
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
    render: function (r) {
      return copyCell(pickupPlace(r));
    },
  });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (r) {
      return mapButton("fly the map to it, ring it and show its layer", function () {
        showRows({ kind: "pickups", rows: [r] }, pickupName(r.category), from, 0);
      }, "show this " + pickupName(r.category) + " pickup on the map");
    },
  });
  return table(columns, rows, {
    sort: sorts[list],
    caption: list + " pickups",
    onRow: function (r) {
      select(pickupSelection(r));
      render();
    },
    rowClass: function (r) {
      return isSelected("pickup", r.name) ? "on" : "";
    },
  });
}

export function renderPickups(body: HTMLElement, params: Record<string, string>): void {
  var list = listOf(params);
  var top = make("section", "dash-card");
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
  var d = box.data!;
  top.appendChild(make("div", "world-census", totals(d)));
  hiddenLine(top, d.hidden_spoilers, "kind not found yet", "kinds not found yet");
  if (d.census.length) top.appendChild(censusTable(d));
  staleLine(top, d.stale);
  var card = make("section", "dash-card");
  body.appendChild(card);
  card.appendChild(
    tabs2(
      LISTS.map(function (l) {
        return { id: l[0], label: l[1], href: hashFor(viewDash("pickups", changed(params, { view: l[0] === "remaining" ? "" : l[0] }))) };
      }),
      list,
      undefined,
      "which pickups to list"
    )
  );
  var bar = filterBar(card);
  bar.appendChild(
    selectField(
      "kind",
      "world-pickups-group",
      params.group || "",
      [["", "every kind"] as [string, string]].concat(
        d.census.map(function (c): [string, string] {
          return [c.category, c.label];
        })
      ),
      function (v) {
        edit(changed(params, { group: v }));
      }
    )
  );
  var rows = d.rows;
  if (!rows.length) {
    empty(card, list === "collected" ? "nothing collected yet" : "no pickup left" + (params.group ? " of this kind" : ""));
    return;
  }
  var line = make("div", "world-census");
  line.appendChild(make("span", "", counted(rows.length, "pickup") + (d.where ? " · nearest to " + d.where : "")));
  var paired = params.group
    ? []
    : d.census.filter(function (c) {
        return !!c.pedestal_of;
      });
  line.appendChild(
    button("show all on map", function () {
      var dash = state.dash;
      toMap(function () {
        showRows({ kind: "pickups", rows: rows }, list + " pickups", dash);
      });
    }, { map: true, title: "ring every row on the map and list them beside it" })
  );
  card.appendChild(line);
  if (paired.length) {
    note(
      card,
      paired
        .map(function (c) {
          return c.label;
        })
        .join(", ") + " are listed with what stands on them, so this list is shorter than the kinds table adds up to"
    );
  }
  var grid = listTable(rows, list);
  card.appendChild(grid);
  capped(card, grid, rows.length, "pickups-" + list, "pickup");
}
