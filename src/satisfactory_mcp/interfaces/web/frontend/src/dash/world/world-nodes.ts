/* World > nodes and fields: the node finder as tables.
 * See docs/world-finders_contract.md §2.1 and §2.2. */

import { appendNote, button, chip, empty, statusChip, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { showRows } from "../../map/tools/finder";
import { fieldLabel, fieldSelection, nodeLabel, nodeRate, nodeSelection, resourceOptions, worldUrl } from "./world-finds";
import { formatNumber, metres, perMin } from "../../kit/format";
import { isSelected } from "../../app/selection";
import { state } from "../../app/state";
import { counted, NODE_KIND, WORDS } from "../../kit/words";
import { mapButton } from "../actions";
import {
  copyCell,
  distanceColumn,
  filterBar,
  loaded,
  numericColumn,
  openRowsOnMap,
  paramSetter,
  regionCell,
  selectAndRender,
  selectField,
  showAllToggle,
  staleLine,
  staleText,
  textField,
  waiting,
  want,
} from "./world-kit";

import type { Column, SortState } from "../../kit/dashkit";
import type { FoundField, FoundNode, NodeFindResponse, TableAge } from "../../api/shapes";
import type { FinderResults } from "../../map/tools/finder";

const nodesBox = loaded<NodeFindResponse>();

const sorts: Record<string, SortState> = {
  nodes: { key: "rate", desc: true },
  near: { key: "distance", desc: false },
  fields: { key: "total", desc: true },
  fieldsNear: { key: "distance", desc: false },
};

const PURITIES: [string, string][] = [
  ["", "any purity"],
  ["pure", "pure"],
  ["normal", "normal"],
  ["impure", "impure"],
];

const STATUSES: [string, string][] = [
  ["", "free or tapped"],
  ["free", WORDS.free],
  ["tapped", WORDS.tapped],
];

function kinds(): [string, string][] {
  return [["", "any kind"] as [string, string]].concat(
    Object.keys(NODE_KIND).map(function (kind): [string, string] {
      return [kind, NODE_KIND[kind]!];
    })
  );
}

/* "mixed" adds items and m³ together, so its total carries a star. */
function rateWithUnit(value: number, unit: string): string {
  if (unit === "/min") return perMin(value);
  if (unit === "m3/min") return formatNumber(value, 1) + " m³/min";
  return perMin(value) + "*";
}

function byDistance<R extends { distance_m: number | null }>(rows: R[], near: boolean): R[] {
  if (!near) return rows;
  return rows.slice().sort(function (a, b) {
    return (a.distance_m === null ? Infinity : a.distance_m) - (b.distance_m === null ? Infinity : b.distance_m);
  });
}

export function nodeTable(rows: FoundNode[], near: boolean, stale?: TableAge | null): HTMLElement {
  const from = state.dash;
  const drift = stale ? staleText(stale) : "";
  const columns: Column<FoundNode>[] = [
    {
      key: "node",
      label: WORDS.node,
      sort: function (node) {
        return node.resource_name + " " + node.purity;
      },
      render: function (node) {
        const cell = make("span", "", node.resource_name);
        cell.appendChild(make("span", "dash-sub", node.purity + (node.kind === "node" ? "" : " · " + (NODE_KIND[node.kind] || node.kind))));
        return cell;
      },
    },
    {
      key: "status",
      label: "status",
      sort: function (node) {
        return node.status;
      },
      render: function (node) {
        const cell = make("span", "world-chips");
        cell.appendChild(statusChip(node.status));
        if (node.moved) cell.appendChild(chip("moved", "mid", drift || "this node's position differs in the save"));
        return cell;
      },
    },
    numericColumn<FoundNode>(
      "rate",
      "per min",
      function (node) {
        return node.rate;
      },
      { render: nodeRate, title: "at 100% clock with the best extractor this world has; fluids in m³" }
    ),
    {
      key: "occupant",
      label: "extractor",
      render: function (node) {
        return node.occupant ? node.occupant + (node.occupant_off ? " (off)" : "") : "–";
      },
    },
    {
      key: "region",
      label: "region",
      sort: function (node) {
        return node.region ? node.region.name : "";
      },
      render: function (node) {
        return regionCell(node.region);
      },
    },
    { key: "grid", label: "grid", render: function (node) { return node.grid || "–"; } },
  ];
  if (near) columns.push(distanceColumn<FoundNode>());
  columns.push({
    key: "selector",
    label: "id",
    render: function (node) {
      return copyCell("node:" + node.name, "copy");
    },
  });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (node) {
      return mapButton("fly the map to it and ring it", function () {
        showRows({ kind: "nodes", rows: [node] }, nodeLabel(node), from, 0);
      }, "show " + nodeLabel(node) + " on the map");
    },
  });
  return table(columns, rows, {
    sort: near ? sorts.near : sorts.nodes,
    caption: "resource nodes",
    onRow: selectAndRender(nodeSelection),
    rowClass: function (node) {
      return (isSelected("node", node.id) ? "on " : "") + (node.spoiler ? "world-locked" : "");
    },
  });
}

function fieldTable(rows: FoundField[], near: boolean): HTMLElement {
  const from = state.dash;
  const columns: Column<FoundField>[] = [
    {
      key: "field",
      label: WORDS.field,
      sort: function (field) {
        return fieldLabel(field);
      },
      render: function (field) {
        const cell = make("span", "", field.resources.join(" + "));
        cell.appendChild(
          make(
            "span",
            "dash-sub",
            Object.keys(field.purities)
              .map(function (purity) {
                return field.purities[purity] + " " + purity;
              })
              .join(" · ")
          )
        );
        return cell;
      },
    },
    numericColumn<FoundField>("size", "nodes", function (field) {
      return field.size;
    }),
    numericColumn<FoundField>(
      "total",
      "total",
      function (field) {
        return field.total;
      },
      {
        title: "per min at 100% clock with the best extractor this world has",
        render: function (field) {
          return perMin(field.total, false);
        },
      }
    ),
    numericColumn<FoundField>(
      "free",
      WORDS.free,
      function (field) {
        return field.free;
      },
      {
        title: "per min on nodes with no extractor that this world can tap",
        render: function (field) {
          return perMin(field.free, false);
        },
      }
    ),
    numericColumn<FoundField>(
      "spread",
      "spread",
      function (field) {
        return field.spread_m;
      },
      {
        title: "widest distance between two of its nodes",
        render: function (field) {
          return metres(field.spread_m);
        },
      }
    ),
    {
      key: "region",
      label: "region",
      sort: function (field) { return field.region || ""; },
      render: function (field) {
        const cell = make("span", "", field.region || "off the map");
        cell.appendChild(make("span", "dash-sub", [field.grid, field.direction].filter(Boolean).join(" · ")));
        if (field.locked) cell.appendChild(chip(WORDS.locked, "muted", "some of its nodes need an extractor not unlocked yet"));
        return cell;
      },
    },
  ];
  if (near) columns.push(distanceColumn<FoundField>());
  columns.push({ key: "selector", label: "id", render: function (field) { return copyCell(field.selector, "copy"); } });
  columns.push({
    key: "map",
    label: "",
    align: "right",
    render: function (field) {
      return mapButton("fly the map to the field and ring its nodes", function () {
        showRows({ kind: "fields", rows: [field] }, fieldLabel(field), from, 0);
      }, "show " + fieldLabel(field) + " on the map");
    },
  });
  return table(columns, rows, {
    sort: near ? sorts.fieldsNear : sorts.fields,
    caption: "fields",
    onRow: selectAndRender(fieldSelection),
    rowClass: function (field) {
      return (isSelected("field", field.key) ? "on " : "") + (field.spoiler ? "world-locked" : "");
    },
  });
}

function filters(card: HTMLElement, params: Record<string, string>): void {
  const bar = filterBar(card);
  const set = paramSetter(params);
  bar.appendChild(
    selectField("resource", "world-resource", params.resource || "", resourceOptions("any resource", params.resource || ""), set("resource"))
  );
  bar.appendChild(selectField("purity", "world-purity", params.purity || "", PURITIES, set("purity")));
  bar.appendChild(selectField("kind", "world-kind", params.kind || "", kinds(), set("kind")));
  bar.appendChild(selectField("status", "world-status", params.status || "", STATUSES, set("status")));
  bar.appendChild(textField("near", "world-near", params.near || "", "me, x,y, a factory or node:…", set("near", true)));
}

function headline(card: HTMLElement, found: NodeFindResponse, view: string): void {
  const line = make("div", "world-census");
  const counts = view === "fields" ? counted(found.fields.length, WORDS.field) : counted(found.count, WORDS.node);
  const rows = view === "fields" ? found.fields.length : found.nodes.length;
  line.appendChild(
    make("span", "", rows ? counts + " · " + rateWithUnit(found.total, found.unit) + " · " + rateWithUnit(found.free, found.unit) + " free and reachable" : counts)
  );
  if (rows) {
    line.appendChild(
      button("show all on map", function () {
        const shown: FinderResults =
          view === "fields" ? { kind: "fields", rows: byDistance(found.fields, !!found.where) } : { kind: "nodes", rows: byDistance(found.nodes, !!found.where) };
        openRowsOnMap(shown, (view === "fields" ? WORDS.field + "s" : WORDS.node + "s") + (found.where ? " near " + found.where : " · " + found.description));
      }, { map: true, title: "ring every row on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  let caveats: string[] = [];
  if (found.unit === "mixed") caveats.push("* items and m³ of fluid added together; choose one resource for a true total");
  if (found.selectors.length) {
    const selectors = make("p", "dash-note world-selectors");
    selectors.appendChild(document.createTextNode("as selectors: "));
    found.selectors.forEach(function (selector) {
      selectors.appendChild(copyCell(selector));
      selectors.appendChild(document.createTextNode(" "));
    });
    if (found.where) selectors.appendChild(document.createTextNode("· near " + found.where));
    card.appendChild(selectors);
  } else if (found.where) appendNote(card, "near " + found.where);
  if (found.elevation) caveats.push("between " + formatNumber(found.elevation[0], 0) + " and " + formatNumber(found.elevation[1], 0) + " m up");
  if (found.water && found.water.pumps) {
    caveats.push(
      counted(found.water.pumps, "water pump spot") +
        (found.water.per_pump_m3_min === null ? "" : " · " + formatNumber(found.water.per_pump_m3_min, 1) + " m³/min each") +
        (found.water.sea_level_m === null ? "" : " · sea level " + formatNumber(found.water.sea_level_m, 0) + " m")
    );
  }
  caveats = caveats.concat(found.notes);
  if (found.save_error) caveats.push(found.save_error + "; occupancy unknown");
  if (caveats.length) appendNote(card, caveats.join(" · "));
  staleLine(card, found.stale);
}

export function renderNodes(body: HTMLElement, view: "nodes" | "fields", params: Record<string, string>): void {
  const card = make("section", "dash-card");
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
  const found = nodesBox.data!;
  headline(card, found, view);
  const near = !!found.where;
  if (view === "fields") {
    if (!found.fields.length) {
      empty(card, "no field matches these filters");
      return;
    }
    const fields = fieldTable(found.fields, near);
    card.appendChild(fields);
    showAllToggle(card, fields, found.fields.length, "fields", WORDS.field);
    return;
  }
  if (!found.nodes.length) {
    empty(card, "no node matches these filters");
    return;
  }
  const grid = nodeTable(found.nodes, near, found.stale);
  card.appendChild(grid);
  showAllToggle(card, grid, found.nodes.length, "nodes", WORDS.node);
}
