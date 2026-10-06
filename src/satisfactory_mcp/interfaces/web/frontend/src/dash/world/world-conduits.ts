/* World > conduits: belt and pipe runs near a place, and the fluid networks they belong to.
 * See docs/world-finders_contract.md §2.3. */

import { appendNote, button, empty, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { CONDUIT_RADIUS_M, showRows } from "../../map/tools/finder";
import { carriesText, runLabel, runSelection, worldUrl } from "./world-finds";
import { coords, count, formatNumber, metres, roundHalfEven } from "../../kit/format";
import { hashFor } from "../../map/map";
import { go } from "../../app/nav";
import { isSelected } from "../../app/selection";
import { state } from "../../app/state";
import { withoutToolHints } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";
import { mapButton } from "../actions";
import {
  copyCell,
  distanceColumn,
  filterBar,
  goToWorldParams,
  loaded,
  numericColumn,
  openRowsOnMap,
  paramSetter,
  selectAndRender,
  selectField,
  showAllToggle,
  textField,
  viewDash,
  waiting,
  want,
  withParams,
} from "./world-kit";

import type { Column, SortState } from "../../kit/dashkit";
import type { ConduitsResponse, NetworkRow, RunEnd, RunRow } from "../../api/shapes";

const PAGE_SIZE = 200;

const box = loaded<ConduitsResponse>();

const sorts: Record<string, SortState> = {
  runs: { key: "length", desc: true },
  networks: { key: "length", desc: true },
};

const RADII: [string, string][] = ["100", CONDUIT_RADIUS_M, "500", "1000", "2000"].map(function (radius): [string, string] {
  return [radius, radius + " m"];
});

const KINDS: [string, string][] = [
  ["", "belts and pipes"],
  ["belt", "belts"],
  ["pipe", "pipes"],
];

const KIND_WORD: Record<string, string> = { belt: "belt", lift: "lift", pipe: "pipe" };

const BASIS: Record<string, string> = {
  "machine port": "a machine port",
  pump: "a pump or valve",
  propagated: "its network",
  unresolved: "not known",
  role: "its ends",
  nature: "its kind",
  unknown: "not known",
};

function endText(end: RunEnd): string {
  return coords(end.x_m, end.y_m);
}

function zSpan(low: number, high: number): string {
  return roundHalfEven(low) === roundHalfEven(high) ? formatNumber(low, 0) + " m" : formatNumber(low, 0) + " to " + formatNumber(high, 0) + " m";
}

function recentre(params: Record<string, string>, to: string): void {
  goToWorldParams(withParams(params, { near: to, offset: "", network: "" }));
}

/* A run's end plugged into another run or pipe is a button that searches again from there. */
function plug(cell: HTMLElement, plugged: string | null, params: Record<string, string>): void {
  if (plugged && /^(chain|pipe):/.test(plugged)) {
    cell.appendChild(
      button(plugged.replace(":", " "), function () {
        recentre(params, plugged);
      }, { title: "search again near " + plugged })
    );
  } else cell.appendChild(make("span", "", plugged || "–"));
}

function connects(run: RunRow, params: Record<string, string>): HTMLElement {
  const cell = make("span", "world-via");
  plug(cell, run.a.plugs, params);
  cell.appendChild(make("span", "", run.directed ? " → " : " · "));
  plug(cell, run.b.plugs, params);
  if (run.via.length) cell.appendChild(make("span", "dash-sub", "via " + run.via.join(", ")));
  return cell;
}

function kindWord(kind: string): string {
  return KIND_WORD[kind] || kind;
}

function runTable(rows: RunRow[], params: Record<string, string>): HTMLElement {
  const from = state.dash;
  const columns: Column<RunRow>[] = [
    {
      key: "run",
      label: WORDS.run,
      sort: function (run) {
        return run.id;
      },
      render: function (run) {
        const named = run.label && run.label !== run.id ? run.label : kindWord(run.kind) + " · " + metres(run.length_m);
        const cell = make("span", "", named);
        const sub = make("span", "dash-sub");
        sub.appendChild(copyCell(run.id, run.id.replace(":", " ")));
        cell.appendChild(sub);
        return cell;
      },
    },
    {
      key: "kind",
      label: "kind",
      sort: function (run) {
        return run.kind;
      },
      render: function (run) {
        return kindWord(run.kind) + (run.directed ? "" : ", no direction");
      },
    },
    numericColumn<RunRow>(
      "length",
      "length",
      function (run) {
        return run.length_m;
      },
      {
        render: function (run) {
          return metres(run.length_m);
        },
      }
    ),
    {
      key: "ends",
      label: "ends",
      className: "dash-nowrap",
      render: function (run) {
        const cell = make("span", "", endText(run.a));
        cell.appendChild(make("span", "dash-sub", endText(run.b)));
        return cell;
      },
    },
    numericColumn<RunRow>(
      "z",
      "height",
      function (run) {
        return run.z_min_m;
      },
      {
        render: function (run) {
          return zSpan(run.z_min_m, run.z_max_m);
        },
      }
    ),
    {
      key: "carries",
      label: "carries",
      render: carriesText,
    },
    {
      key: "basis",
      label: "direction from",
      title: "how the flow direction was worked out",
      render: function (run) {
        return run.basis ? BASIS[run.basis] || run.basis : "–";
      },
    },
    {
      key: "via",
      label: "connects",
      render: function (run) {
        return connects(run, params);
      },
    },
    distanceColumn<RunRow>(),
    {
      key: "map",
      label: "",
      align: "right",
      render: function (run) {
        return mapButton("fly the map to it and draw it", function () {
          showRows({ kind: "runs", rows: [run] }, runLabel(run), from, 0);
        }, "show " + runLabel(run) + " on the map");
      },
    },
  ];
  return table(columns, rows, {
    sort: sorts.runs,
    caption: "belt and pipe runs",
    onRow: selectAndRender(runSelection),
    rowClass: function (run) {
      return isSelected("conduit", run.id) ? "on" : "";
    },
  });
}

function networkTable(rows: NetworkRow[], params: Record<string, string>): HTMLElement {
  const columns: Column<NetworkRow>[] = [
    {
      key: "network",
      label: WORDS.network,
      sort: function (network) {
        return network.network === null ? Infinity : network.network;
      },
      render: function (network) {
        return network.network === null ? "–" : "#" + network.network;
      },
    },
    {
      key: "carries",
      label: "carries",
      sort: function (network) {
        return network.carries || "";
      },
      render: function (network) {
        return network.carries || "nothing known";
      },
    },
    numericColumn<NetworkRow>("pieces", "pieces", function (network) {
      return network.pieces;
    }),
    numericColumn<NetworkRow>(
      "length",
      "length",
      function (network) {
        return network.length_m;
      },
      {
        render: function (network) {
          return metres(network.length_m);
        },
      }
    ),
    { key: "z", label: "height", align: "right", render: function (network) { return zSpan(network.z_min_m, network.z_max_m); } },
    {
      key: "touches",
      label: "touches",
      render: function (network) {
        return network.touches.length ? network.touches.join(", ") : "–";
      },
    },
    distanceColumn<NetworkRow>(),
    {
      key: "runs",
      label: "",
      align: "right",
      render: function (network) {
        if (network.network === null) return make("span", "dash-muted", "–");
        const id = String(network.network);
        return button("runs", function () {
          go(viewDash("conduits", withParams(params, { view: "", network: id, offset: "" })));
        }, { title: "list the runs of this network", label: "list the runs of network " + id });
      },
    },
  ];
  return table(columns, rows, { sort: sorts.networks, caption: "fluid networks" });
}

function filters(card: HTMLElement, params: Record<string, string>): void {
  const bar = filterBar(card);
  const set = paramSetter(params, { offset: "" });
  bar.appendChild(textField("near", "world-conduits-near", params.near || "", "me, x,y, a factory, chain:7", set("near", true)));
  bar.appendChild(selectField("within", "world-conduits-radius", params.radius_m || CONDUIT_RADIUS_M, RADII, set("radius_m")));
  bar.appendChild(textField("to", "world-conduits-to", params.to || "", "optional second place", set("to", true)));
  if (params.to) bar.appendChild(selectField("to within", "world-conduits-to-radius", params.to_radius_m || CONDUIT_RADIUS_M, RADII, set("to_radius_m")));
  bar.appendChild(selectField("kind", "world-conduits-kind", params.conduit_kind || "", KINDS, set("conduit_kind")));
}

function census(card: HTMLElement, conduits: ConduitsResponse, params: Record<string, string>): void {
  const line = make("div", "world-census");
  const parts = [counted(conduits.belts, "belt run") + " · " + metres(conduits.belt_m), counted(conduits.pipes, "pipe run") + " · " + metres(conduits.pipe_m)];
  if (conduits.fluids.length) parts.push("carrying " + conduits.fluids.join(", "));
  line.appendChild(make("span", "", parts.join(" · ")));
  if (conduits.runs.length) {
    line.appendChild(
      button("show all on map", function () {
        openRowsOnMap({ kind: "runs", rows: conduits.runs }, "runs near " + (conduits.where || "here"));
      }, { map: true, title: "draw every run on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  let scope = "near " + (conduits.where || "–") + " within " + metres(conduits.radius_m);
  if (conduits.where_to) scope += " · to " + conduits.where_to + (conduits.to_radius_m === null ? "" : " within " + metres(conduits.to_radius_m));
  if (params.network) scope = WORDS.network + " #" + params.network;
  else if (params.view === "networks") scope = "every fluid " + WORDS.network + ", distance from " + (conduits.where || "you");
  appendNote(card, scope);
  conduits.bridged.forEach(function (text) {
    appendNote(card, withoutToolHints(text));
  });
  conduits.notes.forEach(function (text) {
    appendNote(card, text);
  });
}

function pager(card: HTMLElement, conduits: ConduitsResponse, params: Record<string, string>): void {
  const end = conduits.offset + conduits.runs.length;
  if (conduits.offset === 0 && end >= conduits.total) return;
  const row = make("div", "world-more");
  row.appendChild(make("span", "dash-note", count(conduits.offset + 1) + " to " + count(end) + " of " + count(conduits.total)));
  if (conduits.offset > 0) {
    row.appendChild(
      button("previous " + PAGE_SIZE, function () {
        goToWorldParams(withParams(params, { offset: conduits.offset > PAGE_SIZE ? String(conduits.offset - PAGE_SIZE) : "" }));
      })
    );
  }
  if (end < conduits.total) {
    row.appendChild(
      button("next " + PAGE_SIZE, function () {
        goToWorldParams(withParams(params, { offset: String(end) }));
      })
    );
  }
  card.appendChild(row);
}

export function renderConduits(body: HTMLElement, params: Record<string, string>): void {
  const card = make("section", "dash-card");
  body.appendChild(card);
  filters(card, params);
  const view = params.view === "networks" ? "networks" : "runs";
  card.appendChild(
    subTabs(
      [
        { id: "runs", label: "runs" },
        { id: "networks", label: "networks" },
      ].map(function (tab) {
        const patch: Record<string, string> = tab.id === "runs" ? { view: "", offset: "" } : { view: tab.id, offset: "", network: "" };
        return { id: tab.id, label: tab.label, href: hashFor(viewDash("conduits", withParams(params, patch))) };
      }),
      view,
      undefined,
      "runs or networks"
    )
  );
  want(
    "world-conduits",
    box,
    worldUrl("/api/world/conduits", {
      near: params.near || "",
      radius_m: params.radius_m || "",
      to: params.to || "",
      to_radius_m: params.to ? params.to_radius_m || "" : "",
      conduit_kind: params.conduit_kind || "",
      view: view === "networks" ? "networks" : "",
      network: params.network || "",
      offset: params.offset || "",
      limit: String(PAGE_SIZE),
    })
  );
  if (waiting(card, box, view === "networks" ? "fluid networks" : "belt and pipe runs")) return;
  const conduits = box.data!;
  census(card, conduits, params);
  if (view === "networks") {
    if (!conduits.networks.length) {
      empty(card, "no fluid network here");
      return;
    }
    const networks = networkTable(conduits.networks, params);
    card.appendChild(networks);
    showAllToggle(card, networks, conduits.networks.length, "networks", WORDS.network);
    return;
  }
  if (!conduits.runs.length) {
    empty(card, "no belt or pipe run here", "widen the radius or search near another place");
    return;
  }
  const runs = runTable(conduits.runs, params);
  card.appendChild(runs);
  showAllToggle(card, runs, conduits.runs.length, "runs", WORDS.run);
  pager(card, conduits, params);
}
