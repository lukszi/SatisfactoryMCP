/* World > conduits: belt and pipe runs near a place, and the fluid networks they belong to.
 * See docs/world-finders_contract.md §2.3. */

import { button, empty, note, table, tabs2 } from "./dashkit";
import { mapButton, render, toMap } from "./dashboard";
import { make } from "./dom";
import { coords, metres, runLabel, runSelection, showRows, worldUrl } from "./finder";
import { count, num, perMin } from "./format";
import { hashFor } from "./map";
import { go } from "./nav";
import { isSelected, select } from "./selection";
import { state } from "./state";
import { capped, changed, copyCell, distanceColumn, edit, filterBar, loaded, selectField, textField, viewDash, waiting, want } from "./world";
import { counted, W } from "./words";

import type { Column, SortState } from "./dashkit";
import type { ConduitsResponse, NetworkRow, RunEnd, RunRow } from "./api-shapes";

var PAGE = 200;

var box = loaded<ConduitsResponse>();

var sorts: Record<string, SortState> = {
  runs: { key: "distance", desc: false },
  networks: { key: "distance", desc: false },
};

var RADII: [string, string][] = ["100", "250", "500", "1000", "2000"].map(function (r): [string, string] {
  return [r, r + " m"];
});

var KINDS: [string, string][] = [
  ["", "belts and pipes"],
  ["belt", "belts"],
  ["pipe", "pipes"],
];

var KIND_WORD: Record<string, string> = { belt: "belt", lift: "lift", pipe: "pipe" };

function endText(e: RunEnd): string {
  return coords(e.x_m, e.y_m) + (e.plugs ? " · " + e.plugs : "");
}

function zSpan(lo: number, hi: number): string {
  return Math.round(lo) === Math.round(hi) ? num(lo, 0) + " m" : num(lo, 0) + " to " + num(hi, 0) + " m";
}

function recentre(params: Record<string, string>, to: string): void {
  edit(changed(params, { near: to, offset: "", network: "" }));
}

function connects(r: RunRow, params: Record<string, string>): HTMLElement {
  var cell = make("span", "world-via");
  if (!r.via.length) cell.textContent = "–";
  r.via.forEach(function (v) {
    if (/^(chain|pipe):/.test(v)) {
      cell.appendChild(
        button(v, function () {
          recentre(params, v);
        }, { title: "search again near " + v })
      );
    } else cell.appendChild(make("span", "", v));
  });
  return cell;
}

function runTable(rows: RunRow[], params: Record<string, string>): HTMLElement {
  var from = state.dash;
  var columns: Column<RunRow>[] = [
    {
      key: "run",
      label: W.run,
      sort: function (r) {
        return r.id;
      },
      render: function (r) {
        var cell = make("span");
        cell.appendChild(copyCell(r.id));
        if (r.label && r.label !== r.id) cell.appendChild(make("span", "dash-sub", r.label));
        return cell;
      },
    },
    {
      key: "kind",
      label: "kind",
      sort: function (r) {
        return r.kind;
      },
      render: function (r) {
        return (KIND_WORD[r.kind] || r.kind) + (r.directed ? "" : ", no direction");
      },
    },
    {
      key: "length",
      label: "length",
      align: "right",
      sort: function (r) {
        return r.length_m;
      },
      render: function (r) {
        return metres(r.length_m);
      },
    },
    {
      key: "ends",
      label: "ends",
      className: "dash-nowrap",
      render: function (r) {
        var cell = make("span", "", endText(r.a));
        cell.appendChild(make("span", "dash-sub", endText(r.b)));
        return cell;
      },
    },
    {
      key: "z",
      label: "height",
      align: "right",
      sort: function (r) {
        return r.z_min_m;
      },
      render: function (r) {
        return zSpan(r.z_min_m, r.z_max_m);
      },
    },
    {
      key: "carries",
      label: "carries",
      render: function (r) {
        var parts: string[] = [];
        if (r.carries) parts.push(r.carries);
        else if (r.kind === "pipe") parts.push("nothing known");
        if (r.rate !== null) parts.push((r.kind === "pipe" ? num(r.rate, 0) + " m³/min" : perMin(r.rate)) + " max");
        return parts.length ? parts.join(" · ") : "–";
      },
    },
    {
      key: "basis",
      label: "basis",
      title: "how the flow direction was worked out",
      render: function (r) {
        return r.basis || "–";
      },
    },
    {
      key: "via",
      label: "connects",
      render: function (r) {
        return connects(r, params);
      },
    },
    distanceColumn<RunRow>(),
    {
      key: "map",
      label: "",
      align: "right",
      render: function (r) {
        return mapButton("fly the map to it and draw it", function () {
          showRows({ kind: "runs", rows: [r] }, runLabel(r), from, 0);
        }, "show " + runLabel(r) + " on the map");
      },
    },
  ];
  return table(columns, rows, {
    sort: sorts.runs,
    caption: "belt and pipe runs",
    onRow: function (r) {
      select(runSelection(r));
      render();
    },
    rowClass: function (r) {
      return isSelected("conduit", r.id) ? "on" : "";
    },
  });
}

function networkTable(rows: NetworkRow[], params: Record<string, string>): HTMLElement {
  var columns: Column<NetworkRow>[] = [
    {
      key: "network",
      label: W.network,
      sort: function (n) {
        return n.network === null ? Infinity : n.network;
      },
      render: function (n) {
        return n.network === null ? "–" : "#" + n.network;
      },
    },
    {
      key: "carries",
      label: "carries",
      sort: function (n) {
        return n.carries || "";
      },
      render: function (n) {
        return n.carries || "nothing known";
      },
    },
    { key: "pieces", label: "pieces", align: "right", sort: function (n) { return n.pieces; }, render: function (n) { return count(n.pieces); } },
    { key: "length", label: "length", align: "right", sort: function (n) { return n.length_m; }, render: function (n) { return metres(n.length_m); } },
    { key: "z", label: "height", align: "right", render: function (n) { return zSpan(n.z_min_m, n.z_max_m); } },
    {
      key: "touches",
      label: "touches",
      render: function (n) {
        return n.touches.length ? n.touches.join(", ") : "–";
      },
    },
    distanceColumn<NetworkRow>(),
    {
      key: "runs",
      label: "",
      align: "right",
      render: function (n) {
        if (n.network === null) return make("span", "dash-muted", "–");
        var id = String(n.network);
        return button("runs", function () {
          go(viewDash("conduits", changed(params, { view: "", network: id, offset: "" })));
        }, { title: "list the runs of this network", label: "list the runs of network " + id });
      },
    },
  ];
  return table(columns, rows, { sort: sorts.networks, caption: "fluid networks" });
}

function filters(card: HTMLElement, params: Record<string, string>): void {
  var bar = filterBar(card);
  function set(key: string, soon?: boolean): (v: string) => void {
    return function (v) {
      var patch: Record<string, string> = { offset: "" };
      patch[key] = v;
      edit(changed(params, patch), soon);
    };
  }
  bar.appendChild(textField("near", "world-conduits-near", params.near || "", "me, x,y, a factory, chain:7", set("near", true)));
  bar.appendChild(selectField("within", "world-conduits-radius", params.radius_m || "250", RADII, set("radius_m")));
  bar.appendChild(textField("to", "world-conduits-to", params.to || "", "optional second place", set("to", true)));
  if (params.to) bar.appendChild(selectField("to within", "world-conduits-to-radius", params.to_radius_m || "250", RADII, set("to_radius_m")));
  bar.appendChild(selectField("kind", "world-conduits-kind", params.conduit_kind || "", KINDS, set("conduit_kind")));
}

function census(card: HTMLElement, d: ConduitsResponse, params: Record<string, string>): void {
  var line = make("div", "world-census");
  var parts = [counted(d.belts, "belt run") + " · " + metres(d.belt_m), counted(d.pipes, "pipe run") + " · " + metres(d.pipe_m)];
  if (d.fluids.length) parts.push("carrying " + d.fluids.join(", "));
  line.appendChild(make("span", "", parts.join(" · ")));
  if (d.runs.length) {
    line.appendChild(
      button("show all on map", function () {
        var dash = state.dash;
        toMap(function () {
          showRows({ kind: "runs", rows: d.runs }, "runs near " + (d.where || "here"), dash);
        });
      }, { map: true, title: "draw every run on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  var where = "near " + (d.where || "–") + " within " + metres(d.radius_m);
  if (d.where_to) where += " · to " + d.where_to + (d.to_radius_m === null ? "" : " within " + metres(d.to_radius_m));
  if (params.network) where += " · " + W.network + " #" + params.network;
  note(card, where);
  d.bridged.forEach(function (t) {
    note(card, t);
  });
  d.notes.forEach(function (t) {
    note(card, t);
  });
}

function pager(card: HTMLElement, d: ConduitsResponse, params: Record<string, string>): void {
  var end = d.offset + d.runs.length;
  if (d.offset === 0 && end >= d.total) return;
  var row = make("div", "world-more");
  row.appendChild(make("span", "dash-note", count(d.offset + 1) + " to " + count(end) + " of " + count(d.total)));
  if (d.offset > 0) {
    row.appendChild(
      button("previous " + PAGE, function () {
        edit(changed(params, { offset: d.offset > PAGE ? String(d.offset - PAGE) : "" }));
      })
    );
  }
  if (end < d.total) {
    row.appendChild(
      button("next " + PAGE, function () {
        edit(changed(params, { offset: String(end) }));
      })
    );
  }
  card.appendChild(row);
}

export function renderConduits(body: HTMLElement, params: Record<string, string>): void {
  var card = make("section", "dash-card");
  body.appendChild(card);
  filters(card, params);
  var view = params.view === "networks" ? "networks" : "runs";
  card.appendChild(
    tabs2(
      [
        { id: "runs", label: "runs" },
        { id: "networks", label: "networks" },
      ].map(function (t) {
        var patch: Record<string, string> = t.id === "runs" ? { view: "", offset: "" } : { view: t.id, offset: "", network: "" };
        return { id: t.id, label: t.label, href: hashFor(viewDash("conduits", changed(params, patch))) };
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
      limit: String(PAGE),
    })
  );
  if (waiting(card, box, view === "networks" ? "fluid networks" : "belt and pipe runs")) return;
  var d = box.data!;
  census(card, d, params);
  if (view === "networks") {
    if (!d.networks.length) {
      empty(card, "no fluid network here");
      return;
    }
    var nets = networkTable(d.networks, params);
    card.appendChild(nets);
    capped(card, nets, d.networks.length, "networks", W.network);
    return;
  }
  if (!d.runs.length) {
    empty(card, "no belt or pipe run here", "widen the radius or search near another place");
    return;
  }
  var runs = runTable(d.runs, params);
  card.appendChild(runs);
  capped(card, runs, d.runs.length, "runs", W.run);
  pager(card, d, params);
}
