/* World > nodes and fields: the node finder as tables.
 * See docs/world-finders_contract.md §2.1 and §2.2. */

import { button, chip, empty, note, statusChip, table } from "./dashkit";
import { mapButton, render, toMap } from "./dashboard";
import { make } from "./dom";
import {
  fieldLabel,
  fieldSelection,
  nodeLabel,
  nodeRate,
  nodeSelection,
  resourceOptions,
  showRows,
  worldUrl,
} from "./finder";
import { count, metres, num, perMin } from "./format";
import { isSelected, select } from "./selection";
import { state } from "./state";
import {
  capped,
  changed,
  copyCell,
  distanceColumn,
  edit,
  filterBar,
  loaded,
  regionCell,
  selectField,
  staleLine,
  staleText,
  textField,
  waiting,
  want,
} from "./world";
import { counted, NODE_KIND, W } from "./words";

import type { Column, SortState } from "./dashkit";
import type { FoundField, FoundNode, NodeFindResponse, TableAge } from "./api-shapes";
import type { Shown } from "./finder";

var nodesBox = loaded<NodeFindResponse>();

var sorts: Record<string, SortState> = {
  nodes: { key: "rate", desc: true },
  near: { key: "distance", desc: false },
  fields: { key: "total", desc: true },
  fieldsNear: { key: "distance", desc: false },
};

var PURITIES: [string, string][] = [
  ["", "any purity"],
  ["pure", "pure"],
  ["normal", "normal"],
  ["impure", "impure"],
];

var STATUSES: [string, string][] = [
  ["", "free or tapped"],
  ["free", W.free],
  ["tapped", W.tapped],
];

function kinds(): [string, string][] {
  return [["", "any kind"] as [string, string]].concat(
    Object.keys(NODE_KIND).map(function (k): [string, string] {
      return [k, NODE_KIND[k]!];
    })
  );
}

function rate(value: number, unit: string): string {
  if (unit === "/min") return perMin(value);
  if (unit === "m3/min") return num(value, 1) + " m³/min";
  return perMin(value) + "*";
}

function byDistance<R extends { distance_m: number | null }>(rows: R[], near: boolean): R[] {
  if (!near) return rows;
  return rows.slice().sort(function (a, b) {
    return (a.distance_m === null ? Infinity : a.distance_m) - (b.distance_m === null ? Infinity : b.distance_m);
  });
}

function openRows(set: Shown, title: string, seed?: number): void {
  var dash = state.dash;
  toMap(function () {
    showRows(set, title, dash, seed);
  });
}

function pickNode(n: FoundNode): void {
  select(nodeSelection(n));
  render();
}

export function nodeTable(rows: FoundNode[], near: boolean, stale?: TableAge | null): HTMLElement {
  var from = state.dash;
  var drift = stale ? staleText(stale) : "";
  var columns: Column<FoundNode>[] = [
    {
      key: "node",
      label: W.node,
      sort: function (n) {
        return n.resource_name + " " + n.purity;
      },
      render: function (n) {
        var cell = make("span", "", n.resource_name);
        cell.appendChild(make("span", "dash-sub", n.purity + (n.kind === "node" ? "" : " · " + (NODE_KIND[n.kind] || n.kind))));
        return cell;
      },
    },
    {
      key: "status",
      label: "status",
      sort: function (n) {
        return n.status;
      },
      render: function (n) {
        var cell = make("span", "world-chips");
        cell.appendChild(statusChip(n.status));
        if (n.moved) cell.appendChild(chip("moved", "mid", drift || "this node's position differs in the save"));
        return cell;
      },
    },
    {
      key: "rate",
      label: "per min",
      align: "right",
      title: "at 100% clock with the best extractor this world has; fluids in m³",
      sort: function (n) {
        return n.rate;
      },
      render: nodeRate,
    },
    {
      key: "occupant",
      label: "extractor",
      render: function (n) {
        return n.occupant ? n.occupant + (n.occupant_off ? " (off)" : "") : "–";
      },
    },
    {
      key: "region",
      label: "region",
      sort: function (n) {
        return n.region ? n.region.name : "";
      },
      render: function (n) {
        return regionCell(n.region);
      },
    },
    { key: "grid", label: "grid", render: function (n) { return n.grid || "–"; } },
  ];
  if (near) columns.push(distanceColumn<FoundNode>());
  columns.push({
    key: "selector",
    label: "id",
    render: function (n) {
      return copyCell("node:" + n.name, "copy");
    },
  });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (n) {
      return mapButton("fly the map to it and ring it", function () {
        showRows({ kind: "nodes", rows: [n] }, nodeLabel(n), from, 0);
      }, "show " + nodeLabel(n) + " on the map");
    },
  });
  return table(columns, rows, {
    sort: near ? sorts.near : sorts.nodes,
    caption: "resource nodes",
    onRow: pickNode,
    rowClass: function (n) {
      return (isSelected("node", n.id) ? "on " : "") + (n.spoiler ? "world-locked" : "");
    },
  });
}

function fieldTable(rows: FoundField[], near: boolean): HTMLElement {
  var from = state.dash;
  var columns: Column<FoundField>[] = [
    {
      key: "field",
      label: W.field,
      sort: function (f) {
        return fieldLabel(f);
      },
      render: function (f) {
        var cell = make("span", "", f.resources.join(" + "));
        cell.appendChild(
          make(
            "span",
            "dash-sub",
            Object.keys(f.purities)
              .map(function (p) {
                return f.purities[p] + " " + p;
              })
              .join(" · ")
          )
        );
        return cell;
      },
    },
    { key: "size", label: "nodes", align: "right", sort: function (f) { return f.size; }, render: function (f) { return count(f.size); } },
    {
      key: "total",
      label: "total",
      align: "right",
      title: "per min at 100% clock with the best extractor this world has",
      sort: function (f) { return f.total; },
      render: function (f) { return perMin(f.total, false); },
    },
    {
      key: "free",
      label: W.free,
      align: "right",
      title: "per min on nodes with no extractor that this world can tap",
      sort: function (f) { return f.free; },
      render: function (f) { return perMin(f.free, false); },
    },
    {
      key: "spread",
      label: "spread",
      align: "right",
      title: "widest distance between two of its nodes",
      sort: function (f) { return f.spread_m; },
      render: function (f) { return metres(f.spread_m); },
    },
    {
      key: "region",
      label: "region",
      sort: function (f) { return f.region || ""; },
      render: function (f) {
        var cell = make("span", "", f.region || "off the map");
        cell.appendChild(make("span", "dash-sub", [f.grid, f.direction].filter(Boolean).join(" · ")));
        if (f.locked) cell.appendChild(chip(W.locked, "muted", "some of its nodes need an extractor not unlocked yet"));
        return cell;
      },
    },
  ];
  if (near) columns.push(distanceColumn<FoundField>());
  columns.push({ key: "selector", label: "id", render: function (f) { return copyCell(f.selector, "copy"); } });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (f) {
      return mapButton("fly the map to the field and ring its nodes", function () {
        showRows({ kind: "fields", rows: [f] }, fieldLabel(f), from, 0);
      }, "show " + fieldLabel(f) + " on the map");
    },
  });
  return table(columns, rows, {
    sort: near ? sorts.fieldsNear : sorts.fields,
    caption: "fields",
    onRow: function (f) {
      select(fieldSelection(f));
      render();
    },
    rowClass: function (f) {
      return (isSelected("field", f.key) ? "on " : "") + (f.spoiler ? "world-locked" : "");
    },
  });
}

function filters(card: HTMLElement, params: Record<string, string>): void {
  var bar = filterBar(card);
  function set(key: string, soon?: boolean): (v: string) => void {
    return function (v) {
      var patch: Record<string, string> = {};
      patch[key] = v;
      edit(changed(params, patch), soon);
    };
  }
  bar.appendChild(
    selectField("resource", "world-resource", params.resource || "", resourceOptions("any resource", params.resource || ""), set("resource"))
  );
  bar.appendChild(selectField("purity", "world-purity", params.purity || "", PURITIES, set("purity")));
  bar.appendChild(selectField("kind", "world-kind", params.kind || "", kinds(), set("kind")));
  bar.appendChild(selectField("status", "world-status", params.status || "", STATUSES, set("status")));
  bar.appendChild(textField("near", "world-near", params.near || "", "me, x,y, a factory or node:…", set("near", true)));
}

function headline(card: HTMLElement, d: NodeFindResponse, view: string): void {
  var line = make("div", "world-census");
  var n = view === "fields" ? counted(d.fields.length, W.field) : counted(d.count, W.node);
  var rows = view === "fields" ? d.fields.length : d.nodes.length;
  line.appendChild(make("span", "", rows ? n + " · " + rate(d.total, d.unit) + " · " + rate(d.free, d.unit) + " free and reachable" : n));
  if (rows) {
    line.appendChild(
      button("show all on map", function () {
        var set: Shown = view === "fields" ? { kind: "fields", rows: byDistance(d.fields, !!d.where) } : { kind: "nodes", rows: byDistance(d.nodes, !!d.where) };
        openRows(set, (view === "fields" ? W.field + "s" : W.node + "s") + (d.where ? " near " + d.where : " · " + d.description));
      }, { map: true, title: "ring every row on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  var caveats: string[] = [];
  if (d.unit === "mixed") caveats.push("* items and m³ of fluid added together; choose one resource for a true total");
  if (d.selectors.length) {
    var sel = make("p", "dash-note world-selectors");
    sel.appendChild(document.createTextNode("as selectors: "));
    d.selectors.forEach(function (s) {
      sel.appendChild(copyCell(s));
      sel.appendChild(document.createTextNode(" "));
    });
    if (d.where) sel.appendChild(document.createTextNode("· near " + d.where));
    card.appendChild(sel);
  } else if (d.where) note(card, "near " + d.where);
  if (d.elevation) caveats.push("between " + num(d.elevation[0], 0) + " and " + num(d.elevation[1], 0) + " m up");
  if (d.water && d.water.pumps) {
    caveats.push(
      counted(d.water.pumps, "water pump spot") +
        (d.water.per_pump_m3_min === null ? "" : " · " + num(d.water.per_pump_m3_min, 1) + " m³/min each") +
        (d.water.sea_level_m === null ? "" : " · sea level " + num(d.water.sea_level_m, 0) + " m")
    );
  }
  caveats = caveats.concat(d.notes);
  if (d.save_error) caveats.push(d.save_error + "; occupancy unknown");
  if (caveats.length) note(card, caveats.join(" · "));
  staleLine(card, d.stale);
}

export function renderNodes(body: HTMLElement, view: "nodes" | "fields", params: Record<string, string>): void {
  var card = make("section", "dash-card");
  body.appendChild(card);
  filters(card, params);
  want(
    "world-nodes",
    nodesBox,
    worldUrl("/api/world/nodes", {
      view: view,
      resource: params.resource || "",
      purity: params.purity || "",
      kind: params.kind || "",
      status: params.status || "",
      near: params.near || "",
    })
  );
  if (waiting(card, nodesBox, view)) return;
  var d = nodesBox.data!;
  headline(card, d, view);
  var near = !!d.where;
  if (view === "fields") {
    if (!d.fields.length) {
      empty(card, "no field matches these filters");
      return;
    }
    var fields = fieldTable(d.fields, near);
    card.appendChild(fields);
    capped(card, fields, d.fields.length, "fields", W.field);
    return;
  }
  if (!d.nodes.length) {
    empty(card, "no node matches these filters");
    return;
  }
  var grid = nodeTable(d.nodes, near, d.stale);
  card.appendChild(grid);
  capped(card, grid, d.nodes.length, "nodes", W.node);
}
