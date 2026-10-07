/* World > rank: build sites for one resource, scored. The pane says where the ranking looks
 * and whether only pure nodes count; those become `near:<place>@<m>` and `purity:pure`
 * sources. See docs/world-finders_contract.md §2.2. */

import { appendNote, button, empty, table, toggleButton } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { showRows } from "../../map/tools/finder";
import { resourceOptions, siteSelection, worldUrl } from "./world-finds";
import { coords, count, formatNumber, metres, perMin, roundHalfEven, signed, withUnit } from "../../kit/format";
import { gameXY, map } from "../../map/map";
import { go, goToMapThen } from "../../app/nav";
import { onVitals, vitals } from "../../app/vitals";
import { livePins, onPins, pinName } from "../../chat/pins";
import { isSelected, selected } from "../../app/selection";
import { state } from "../../app/state";
import { notify } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";
import { mapButton, requestRender } from "../actions";
import {
  address,
  copyCell,
  filterBar,
  goToWorldParams,
  loaded,
  numericColumn,
  openRowsOnMap,
  rangeField,
  selectAndRender,
  selectField,
  staleLine,
  viewDash,
  waiting,
  want,
  withParams,
  worldTabShown,
} from "./world-kit";

import type * as L from "leaflet";
import type { Column, SortState } from "../../kit/dashkit";
import type { RankedSite, RankedSitesResponse } from "../../api/shapes";

const SITES_LIMIT = "10";

const RANK_KEYS = ["resource", "at", "within_m", "pure"];

const sitesBox = loaded<RankedSitesResponse>();

const siteSort: SortState = { key: "rank", desc: false };

type Anchor = "" | "hub" | "me" | "pin" | "factory" | "point";

const WITHIN_M = { min: 500, max: 5000, step: 500, fallback: 1000 };

const POINT = /^-?\d+(\.\d+)?,-?\d+(\.\d+)?$/;

const ANCHOR_WORD: Record<Anchor, string> = {
  "": "anywhere",
  hub: "the HUB",
  me: "the player",
  pin: "a pin",
  factory: "a factory",
  point: "a map point",
};

/* A newer pick, or Esc, makes an older click on the map land nowhere. */
let pickGeneration = 0;

function anchorOf(at: string): Anchor {
  const low = at.toLowerCase();
  if (!at) return "";
  if (low === "hub" || low === "me") return low;
  if (/^pin:\d+$/.test(low)) return "pin";
  if (POINT.test(at)) return "point";
  return "factory";
}

function withinM(params: Record<string, string>): number {
  const value = Number(params.within_m);
  return isFinite(value) && value >= WITHIN_M.min && value <= WITHIN_M.max ? value : WITHIN_M.fallback;
}

function rankSources(params: Record<string, string>): string[] {
  const out: string[] = [];
  if (params.at) out.push("near:" + params.at + "@" + withinM(params));
  if (params.pure === "1") out.push("purity:pure");
  return out;
}

function km(m: number): string {
  return formatNumber(m / 1000, 1) + " km";
}

function pins(): [string, string][] {
  return livePins()
    .filter(function (pin) {
      return !pin.gone && pin.x_m !== null && pin.y_m !== null;
    })
    .map(function (pin): [string, string] {
      return [pin.id, pinName(pin)];
    });
}

function factories(): [string, string][] {
  const health = vitals().health;
  return (health ? health.factories : [])
    .map(function (factory): [string, string] {
      return [factory.name, factory.name];
    })
    .sort(function (a, b) {
      return a[1].localeCompare(b[1], undefined, { sensitivity: "base", numeric: true });
    });
}

function selectedPoint(): string {
  const selection = selected();
  if (selection?.x_m === undefined || selection.y_m === undefined) return "";
  return Math.round(selection.x_m) + "," + Math.round(selection.y_m);
}

function pickPointOnMap(params: Record<string, string>): void {
  const mine = ++pickGeneration;
  goToMapThen(function () {
    const container = map.getContainer();
    container.classList.add("map-picking");
    notify("click the map where the ranking should look; Esc cancels");
    function done(): void {
      map.off("click", clicked);
      document.removeEventListener("keydown", key, true);
      container.classList.remove("map-picking");
    }
    function clicked(event: L.LeafletMouseEvent): void {
      done();
      if (mine !== pickGeneration) return;
      const xy = gameXY(event.latlng);
      go(viewDash("rank", withParams(params, { at: Math.round(xy[0]) + "," + Math.round(xy[1]) })));
    }
    function key(event: KeyboardEvent): void {
      if (event.key !== "Escape") return;
      pickGeneration += 1;
      done();
    }
    map.on("click", clicked);
    document.addEventListener("keydown", key, true);
  });
}

function anchorText(params: Record<string, string>, anchor: Anchor): string {
  const at = params.at || "";
  if (anchor === "hub" || anchor === "me") return ANCHOR_WORD[anchor];
  if (anchor === "pin") return at;
  if (anchor === "point") {
    const xy = at.split(",");
    return coords(Number(xy[0]), Number(xy[1]));
  }
  return "“" + at + "”";
}

function rankPane(card: HTMLElement, params: Record<string, string>): void {
  const bar = filterBar(card);
  function setParams(patch: Record<string, string>, debounce?: boolean): void {
    goToWorldParams(withParams(params, patch), debounce);
  }
  bar.appendChild(
    selectField("resource", "world-rank-resource", params.resource || "", resourceOptions("choose a resource", params.resource || ""), function (value) {
      setParams({ resource: value });
    })
  );
  const anchor = anchorOf(params.at || "");
  const pinList = pins();
  const factoryList = factories();
  const kinds = (Object.keys(ANCHOR_WORD) as Anchor[]).filter(function (kind) {
    return kind === anchor || (kind !== "pin" || pinList.length > 0) && (kind !== "factory" || factoryList.length > 0);
  });
  bar.appendChild(
    selectField(
      "near",
      "world-rank-near",
      anchor,
      kinds.map(function (kind): [string, string] {
        return [kind, ANCHOR_WORD[kind]];
      }),
      function (value) {
        const kind = value as Anchor;
        if (kind === "pin") setParams({ at: pinList[0]![0] });
        else if (kind === "factory") setParams({ at: factoryList[0]![0] });
        else if (kind === "point") {
          const at = selectedPoint();
          if (at) setParams({ at: at });
          else pickPointOnMap(params);
        } else setParams({ at: kind });
      }
    )
  );
  if (anchor === "pin") {
    bar.appendChild(selectField("pin", "world-rank-pin", params.at || "", pinList, function (value) { setParams({ at: value }); }));
  } else if (anchor === "factory") {
    const names = factoryList.some(function (factory) { return factory[0] === params.at; })
      ? factoryList
      : [[params.at || "", params.at || ""] as [string, string]].concat(factoryList);
    bar.appendChild(selectField("factory", "world-rank-factory", params.at || "", names, function (value) { setParams({ at: value }); }));
  } else if (anchor === "point") {
    bar.appendChild(
      button("pick on map", function () {
        pickPointOnMap(params);
      }, { title: "click the map to move the point" })
    );
  }
  const within = withinM(params);
  bar.appendChild(
    rangeField("within", "world-rank-within", within, WITHIN_M, km, function (value) {
      setParams({ within_m: value === WITHIN_M.fallback ? "" : String(value) });
    }, { disabled: !anchor, title: anchor ? "how far from " + anchorText(params, anchor) + " a node may be" : "choose a place to rank near" })
  );
  bar.appendChild(
    toggleButton("pure only", params.pure === "1", function () {
      setParams({ pure: params.pure === "1" ? "" : "1" });
    }, { title: "rank fields made only of pure nodes" })
  );
  const kind = (params.pure === "1" ? "pure-node " : "") + "field";
  appendNote(card, "ranks " + (anchor ? kind + "s with a node within " + km(within) + " of " + anchorText(params, anchor) : "every " + kind + " on the map"));
}

/* The old `fields?rank=1` address carried these keys; the rank view keeps them. */
export function fromFieldsRank(params: Record<string, string>): Record<string, string> {
  const kept: Record<string, string> = {};
  RANK_KEYS.forEach(function (key) {
    if (params[key]) kept[key] = params[key]!;
  });
  return kept;
}

function siteColumn(
  key: string,
  label: string,
  pick: (site: RankedSite) => number | null,
  render: (site: RankedSite) => string,
  title?: string
): Column<RankedSite> {
  return numericColumn<RankedSite>(key, label, pick, { render: render, title: title });
}

function siteTable(rows: RankedSite[]): HTMLElement {
  const from = state.dash;
  const columns: Column<RankedSite>[] = [
    siteColumn("rank", "rank", function (site) { return site.rank; }, function (site) { return String(site.rank); }),
    {
      key: "region",
      label: "region",
      sort: function (site) { return site.region || ""; },
      render: function (site) {
        const cell = make("span", "", site.region || "off the map");
        cell.appendChild(make("span", "dash-sub", site.grid));
        return cell;
      },
    },
    siteColumn("score", "score", function (site) { return site.score; }, function (site) { return formatNumber(site.score, 2); }),
    siteColumn("nodes", "nodes", function (site) { return site.nodes; }, function (site) { return count(site.nodes); }),
    siteColumn("untapped", "untapped", function (site) { return site.untapped; }, function (site) { return perMin(site.untapped, false); }, "per min on nodes with no extractor"),
    siteColumn("spread", "spread", function (site) { return site.spread_m; }, function (site) { return metres(site.spread_m); }),
    siteColumn("infra", "to built", function (site) { return site.to_infra_m; }, function (site) { return metres(site.to_infra_m); }, "to the nearest thing already built"),
    siteColumn("purity", "purity", function (site) { return site.purity; }, function (site) { return formatNumber(site.purity, 2); }),
    siteColumn(
      "alt",
      "above refineries",
      function (site) { return site.alt_m; },
      function (site) { return site.alt_m === null ? "–" : signed(site.alt_m, function (value) { return metres(value); }); },
      "height above your refineries: positive means fluid flows downhill to them"
    ),
    siteColumn(
      "rough",
      "rough",
      function (site) { return site.rough_m; },
      function (site) { return site.rough_m === null ? "–" : roundHalfEven(site.rough_m, 1).toFixed(1) + " m"; },
      "how uneven the ground is"
    ),
    siteColumn("slope", "slope", function (site) { return site.slope_deg; }, function (site) { return withUnit(site.slope_deg, 0, "°"); }),
    siteColumn("wet", "water", function (site) { return site.wet_pct; }, function (site) { return withUnit(site.wet_pct, 0, "%"); }, "share of the site under water"),
    { key: "selector", label: "id", render: function (site) { return copyCell(site.selector, "copy"); } },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (site) {
        return mapButton("fly the map to this site", function () {
          showRows({ kind: "sites", rows: [site] }, "site " + site.rank, from, 0);
        }, "show site " + site.rank + " on the map");
      },
    },
  ];
  return table(columns, rows, {
    sort: siteSort,
    caption: "ranked build sites",
    onRow: selectAndRender(siteSelection),
    rowClass: function (site) {
      return isSelected("field", site.selector) ? "on" : "";
    },
  });
}

function renderSites(card: HTMLElement, params: Record<string, string>): void {
  let url = worldUrl("/api/world/sites", { resource: params.resource || "", limit: SITES_LIMIT });
  rankSources(params).forEach(function (source) {
    url = (url + "&source=" + encodeURIComponent(source)) as typeof url;
  });
  want("world-sites", sitesBox, url);
  if (waiting(card, sitesBox, "ranked sites")) return;
  const sites = sitesBox.data!;
  const line = make("div", "world-census");
  const top =
    sites.sites.length < sites.count
      ? "top " + count(sites.sites.length) + " of " + count(sites.count) + " candidate " + WORDS.field + "s"
      : counted(sites.sites.length, "candidate " + WORDS.field);
  line.appendChild(make("span", "", top + " for " + sites.resource_name));
  if (sites.sites.length) {
    line.appendChild(
      button("show all on map", function () {
        openRowsOnMap({ kind: "sites", rows: sites.sites }, sites.resource_name + " sites");
      }, { map: true, title: "ring every site on the map and list them beside it" })
    );
  }
  card.appendChild(line);
  const weights = Object.keys(sites.weights || {});
  if (weights.length) {
    appendNote(
      card,
      "score weights: " +
        weights
          .map(function (weight) {
            return weight.replace(/_/g, " ") + " " + formatNumber(sites.weights[weight]!, 2);
          })
          .join(" · ")
    );
  }
  sites.notes.forEach(function (text) {
    appendNote(card, text);
  });
  staleLine(card, sites.stale);
  if (!sites.sites.length) {
    empty(card, "no build site found for " + sites.resource_name);
    return;
  }
  card.appendChild(siteTable(sites.sites));
}

export function renderRank(body: HTMLElement, params: Record<string, string>): void {
  const card = make("section", "dash-card");
  body.appendChild(card);
  rankPane(card, params);
  if (!params.resource) {
    empty(card, "choose one resource to rank its build sites");
    return;
  }
  renderSites(card, params);
}

function rerank(): void {
  if (worldTabShown() && address().view === "rank") requestRender();
}

onVitals(rerank);
onPins(rerank);
