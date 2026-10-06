/* World > rank: build sites for one resource, scored. The pane says where the ranking looks
 * and whether only pure nodes count; those become `near:<place>@<m>` and `purity:pure`
 * sources. See docs/world-finders_contract.md §2.2. */

import { leaveDashThen, mapButton, render } from "../shell";
import { appendNote, button, empty, table, toggleButton } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { showRows } from "../../map/tools/finder";
import { resourceOptions, siteSelection, worldUrl } from "./world-finds";
import { coords, count, formatNumber, metres, perMin, roundHalfEven, signed, withUnit } from "../../kit/format";
import { map } from "../../map/map";
import { dashParts, go, goToMapThen, subjectQuery } from "../../app/nav";
import { onVitals, vitals } from "../../app/vitals";
import { livePins, onPins, pinName } from "../../chat/pins";
import { isSelected, select, selected } from "../../app/selection";
import { state } from "../../app/state";
import { notify } from "../../kit/toast";
import { changed, copyCell, edit, filterBar, loaded, rangeField, selectField, staleLine, viewDash, waiting, want } from "./world";
import { counted, WORDS } from "../../kit/words";

import type * as L from "leaflet";
import type { Column, SortState } from "../../kit/dashkit";
import type { RankedSite, RankedSitesResponse } from "../../api/shapes";

var SITES_LIMIT = "10";

var RANK_KEYS = ["resource", "at", "within_m", "pure"];

var sitesBox = loaded<RankedSitesResponse>();

var siteSort: SortState = { key: "rank", desc: false };

type Anchor = "" | "hub" | "me" | "pin" | "factory" | "point";

export var WITHIN_M = { min: 500, max: 5000, step: 500, fallback: 1000 };

var POINT = /^-?\d+(\.\d+)?,-?\d+(\.\d+)?$/;

var ANCHOR_WORD: Record<Anchor, string> = {
  "": "anywhere",
  hub: "the HUB",
  me: "the player",
  pin: "a pin",
  factory: "a factory",
  point: "a map point",
};

var picking = 0;

export function anchorOf(at: string): Anchor {
  var low = at.toLowerCase();
  if (!at) return "";
  if (low === "hub" || low === "me") return low;
  if (/^pin:\d+$/.test(low)) return "pin";
  if (POINT.test(at)) return "point";
  return "factory";
}

export function withinM(params: Record<string, string>): number {
  var v = Number(params.within_m);
  return isFinite(v) && v >= WITHIN_M.min && v <= WITHIN_M.max ? v : WITHIN_M.fallback;
}

export function rankSources(params: Record<string, string>): string[] {
  var out: string[] = [];
  if (params.at) out.push("near:" + params.at + "@" + withinM(params));
  if (params.pure === "1") out.push("purity:pure");
  return out;
}

function km(m: number): string {
  return formatNumber(m / 1000, 1) + " km";
}

function pins(): [string, string][] {
  return livePins()
    .filter(function (p) {
      return !p.gone && p.x_m !== null && p.y_m !== null;
    })
    .map(function (p): [string, string] {
      return [p.id, pinName(p)];
    });
}

function factories(): [string, string][] {
  var health = vitals().health;
  return (health ? health.factories : [])
    .map(function (f): [string, string] {
      return [f.name, f.name];
    })
    .sort(function (a, b) {
      return a[1].localeCompare(b[1], undefined, { sensitivity: "base", numeric: true });
    });
}

function selectedPoint(): string {
  var s = selected();
  if (!s || s.x_m === undefined || s.y_m === undefined) return "";
  return Math.round(s.x_m) + "," + Math.round(s.y_m);
}

function pick(params: Record<string, string>): void {
  var mine = ++picking;
  goToMapThen(function () {
    var box = map.getContainer();
    box.classList.add("map-picking");
    notify("click the map where the ranking should look; Esc cancels");
    function done(): void {
      map.off("click", clicked);
      document.removeEventListener("keydown", key, true);
      box.classList.remove("map-picking");
    }
    function clicked(e: L.LeafletMouseEvent): void {
      done();
      if (mine !== picking) return;
      go(viewDash("rank", changed(params, { at: Math.round(e.latlng.lng) + "," + Math.round(-e.latlng.lat) })));
    }
    function key(e: KeyboardEvent): void {
      if (e.key !== "Escape") return;
      picking += 1;
      done();
    }
    map.on("click", clicked);
    document.addEventListener("keydown", key, true);
  });
}

function where(params: Record<string, string>, anchor: Anchor): string {
  var at = params.at || "";
  if (anchor === "hub" || anchor === "me") return ANCHOR_WORD[anchor];
  if (anchor === "pin") return at;
  if (anchor === "point") {
    var xy = at.split(",");
    return coords(Number(xy[0]), Number(xy[1]));
  }
  return "“" + at + "”";
}

function rankPane(card: HTMLElement, params: Record<string, string>): void {
  var bar = filterBar(card);
  bar.appendChild(
    selectField("resource", "world-rank-resource", params.resource || "", resourceOptions("choose a resource", params.resource || ""), function (v) {
      set({ resource: v });
    })
  );
  var anchor = anchorOf(params.at || "");
  var pinList = pins();
  var factoryList = factories();
  var kinds = (Object.keys(ANCHOR_WORD) as Anchor[]).filter(function (k) {
    return k === anchor || (k !== "pin" || pinList.length > 0) && (k !== "factory" || factoryList.length > 0);
  });
  function set(patch: Record<string, string>, soon?: boolean): void {
    edit(changed(params, patch), soon);
  }
  bar.appendChild(
    selectField(
      "near",
      "world-rank-near",
      anchor,
      kinds.map(function (k): [string, string] {
        return [k, ANCHOR_WORD[k]];
      }),
      function (v) {
        var k = v as Anchor;
        if (k === "pin") set({ at: pinList[0]![0] });
        else if (k === "factory") set({ at: factoryList[0]![0] });
        else if (k === "point") {
          var at = selectedPoint();
          if (at) set({ at: at });
          else pick(params);
        } else set({ at: k });
      }
    )
  );
  if (anchor === "pin") {
    bar.appendChild(selectField("pin", "world-rank-pin", params.at || "", pinList, function (v) { set({ at: v }); }));
  } else if (anchor === "factory") {
    var names = factoryList.some(function (f) { return f[0] === params.at; }) ? factoryList : [[params.at || "", params.at || ""] as [string, string]].concat(factoryList);
    bar.appendChild(selectField("factory", "world-rank-factory", params.at || "", names, function (v) { set({ at: v }); }));
  } else if (anchor === "point") {
    bar.appendChild(
      button("pick on map", function () {
        pick(params);
      }, { title: "click the map to move the point" })
    );
  }
  var within = withinM(params);
  bar.appendChild(
    rangeField("within", "world-rank-within", within, WITHIN_M, km, function (v) {
      set({ within_m: v === WITHIN_M.fallback ? "" : String(v) });
    }, { disabled: !anchor, title: anchor ? "how far from " + where(params, anchor) + " a node may be" : "choose a place to rank near" })
  );
  bar.appendChild(
    toggleButton("pure only", params.pure === "1", function () {
      set({ pure: params.pure === "1" ? "" : "1" });
    }, { title: "rank fields made only of pure nodes" })
  );
  var kind = (params.pure === "1" ? "pure-node " : "") + "field";
  appendNote(card, "ranks " + (anchor ? kind + "s with a node within " + km(within) + " of " + where(params, anchor) : "every " + kind + " on the map"));
}

export function fromFieldsRank(params: Record<string, string>): Record<string, string> {
  var kept: Record<string, string> = {};
  RANK_KEYS.forEach(function (k) {
    if (params[k]) kept[k] = params[k]!;
  });
  return kept;
}

function siteTable(rows: RankedSite[]): HTMLElement {
  var from = state.dash;
  function n(key: string, label: string, pick: (s: RankedSite) => number | null, show: (s: RankedSite) => string, title?: string): Column<RankedSite> {
    return {
      key: key,
      label: label,
      align: "right",
      title: title,
      sort: function (s) {
        var v = pick(s);
        return v === null ? Infinity : v;
      },
      render: show,
    };
  }
  var columns: Column<RankedSite>[] = [
    n("rank", "rank", function (s) { return s.rank; }, function (s) { return String(s.rank); }),
    {
      key: "region",
      label: "region",
      sort: function (s) { return s.region || ""; },
      render: function (s) {
        var cell = make("span", "", s.region || "off the map");
        cell.appendChild(make("span", "dash-sub", s.grid));
        return cell;
      },
    },
    n("score", "score", function (s) { return s.score; }, function (s) { return formatNumber(s.score, 2); }),
    n("nodes", "nodes", function (s) { return s.nodes; }, function (s) { return count(s.nodes); }),
    n("untapped", "untapped", function (s) { return s.untapped; }, function (s) { return perMin(s.untapped, false); }, "per min on nodes with no extractor"),
    n("spread", "spread", function (s) { return s.spread_m; }, function (s) { return metres(s.spread_m); }),
    n("infra", "to built", function (s) { return s.to_infra_m; }, function (s) { return metres(s.to_infra_m); }, "to the nearest thing already built"),
    n("purity", "purity", function (s) { return s.purity; }, function (s) { return formatNumber(s.purity, 2); }),
    n("alt", "above refineries", function (s) { return s.alt_m; }, function (s) { return s.alt_m === null ? "–" : signed(s.alt_m, function (v) { return metres(v); }); }, "height above your refineries: positive means fluid flows downhill to them"),
    n("rough", "rough", function (s) { return s.rough_m; }, function (s) { return s.rough_m === null ? "–" : roundHalfEven(s.rough_m, 1).toFixed(1) + " m"; }, "how uneven the ground is"),
    n("slope", "slope", function (s) { return s.slope_deg; }, function (s) { return withUnit(s.slope_deg, 0, "°"); }),
    n("wet", "water", function (s) { return s.wet_pct; }, function (s) { return withUnit(s.wet_pct, 0, "%"); }, "share of the site under water"),
    { key: "selector", label: "id", render: function (s) { return copyCell(s.selector, "copy"); } },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (s) {
        return mapButton("fly the map to this site", function () {
          showRows({ kind: "sites", rows: [s] }, "site " + s.rank, from, 0);
        }, "show site " + s.rank + " on the map");
      },
    },
  ];
  return table(columns, rows, {
    sort: siteSort,
    caption: "ranked build sites",
    onRow: function (s) {
      select(siteSelection(s));
      render();
    },
    rowClass: function (s) {
      return isSelected("field", s.selector) ? "on" : "";
    },
  });
}

function renderSites(card: HTMLElement, params: Record<string, string>): void {
  var url = worldUrl("/api/world/sites", { resource: params.resource || "", limit: SITES_LIMIT });
  rankSources(params).forEach(function (s) {
    url = (url + "&source=" + encodeURIComponent(s)) as typeof url;
  });
  want("world-sites", sitesBox, url);
  if (waiting(card, sitesBox, "ranked sites")) return;
  var d = sitesBox.data!;
  var line = make("div", "world-census");
  var top = d.sites.length < d.count ? "top " + count(d.sites.length) + " of " + count(d.count) + " candidate " + WORDS.field + "s" : counted(d.sites.length, "candidate " + WORDS.field);
  line.appendChild(make("span", "", top + " for " + d.resource_name));
  if (d.sites.length) {
    line.appendChild(
      button("show all on map", function () {
        var dash = state.dash;
        leaveDashThen(function () {
          showRows({ kind: "sites", rows: d.sites }, d.resource_name + " sites", dash);
        });
      }, { map: true, title: "ring every site on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  var weights = Object.keys(d.weights || {});
  if (weights.length) {
    appendNote(
      card,
      "score weights: " +
        weights
          .map(function (k) {
            return k.replace(/_/g, " ") + " " + formatNumber(d.weights[k]!, 2);
          })
          .join(" · ")
    );
  }
  d.notes.forEach(function (t) {
    appendNote(card, t);
  });
  staleLine(card, d.stale);
  if (!d.sites.length) {
    empty(card, "no build site found for " + d.resource_name);
    return;
  }
  card.appendChild(siteTable(d.sites));
}

export function renderRank(body: HTMLElement, params: Record<string, string>): void {
  var card = make("section", "dash-card");
  body.appendChild(card);
  rankPane(card, params);
  if (!params.resource) {
    empty(card, "choose one resource to rank its build sites");
    return;
  }
  renderSites(card, params);
}

function rerank(): void {
  var at = dashParts();
  if (at.tab === "world" && subjectQuery(at.subject).head === "rank") render();
}

onVitals(rerank);
onPins(rerank);
