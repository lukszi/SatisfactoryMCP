/* World > fields > rank: where the ranking looks, and whether only pure nodes count.
 * The settings become `near:<place>@<m>` and `purity:pure` sources. See
 * docs/world-finders_contract.md §2.2. */

import { render } from "./dashboard";
import { button, note, pressed } from "./dashkit";
import { make } from "./dom";
import { coords, num } from "./format";
import { map } from "./map";
import { dashParts, go, onMap, subjectQuery } from "./nav";
import { onVitals, vitals } from "./panel";
import { livePins, onPins, pinName } from "./pins";
import { selected } from "./selection";
import { note as toast } from "./toast";
import { changed, edit, rangeField, selectField, viewDash } from "./world";

import type * as L from "leaflet";

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
  return num(m / 1000, 1) + " km";
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
  onMap(function () {
    var box = map.getContainer();
    box.classList.add("map-picking");
    toast("click the map where the ranking should look; Esc cancels");
    function done(): void {
      map.off("click", clicked);
      document.removeEventListener("keydown", key, true);
      box.classList.remove("map-picking");
    }
    function clicked(e: L.LeafletMouseEvent): void {
      done();
      if (mine !== picking) return;
      go(viewDash("fields", changed(params, { at: Math.round(e.latlng.lng) + "," + Math.round(-e.latlng.lat) })));
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

export function rankPane(card: HTMLElement, params: Record<string, string>): void {
  var pane = make("div", "world-rank");
  var bar = make("div", "world-filters");
  pane.appendChild(bar);
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
    pressed("pure only", params.pure === "1", function () {
      set({ pure: params.pure === "1" ? "" : "1" });
    }, { title: "rank fields made only of pure nodes" })
  );
  card.appendChild(pane);
  var scope = anchor ? "fields with a node within " + km(within) + " of " + where(params, anchor) : "every field on the map";
  note(card, "ranks " + (params.pure === "1" ? "pure-node " : "") + scope);
}

function rerank(): void {
  var at = dashParts();
  if (at.tab === "world" && subjectQuery(at.subject).params.rank === "1") render();
}

onVitals(rerank);
onPins(rerank);
