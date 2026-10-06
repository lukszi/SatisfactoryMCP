/* The factory detail page's level-2 tabs past the overview: flows, machines, power, nodes,
 * links, floors and sites. The address is `factories/<name>/<aspect>`. */

import { get, latest } from "../../api/client";
import { button, chip, empty, error, heading, link, loading, note, table, tabs2, tile } from "../../kit/dashkit";
import { code, make } from "../../kit/dom";
import { count, mw, num, pct, perMin, signed } from "../../kit/format";
import { enterFloors } from "../../map/floors/floors";
import { hashFor } from "../../map/map";
import { showBox } from "../../map/panel";
import { pinThis } from "../../chat/pins";
import { state } from "../../app/state";
import { counted, W } from "../../kit/words";
import { mapButton, pointButton, render } from "../shell";

import type { SortState } from "../../kit/dashkit";
import type {
  AspectBalance,
  AspectCount,
  AspectLink,
  AspectMachine,
  AspectNode,
  FactoryAspectsResponse,
  FactoryFloorsResponse,
  FloorBand,
  FloorPlatform,
  SiteRow,
  SitesResponse,
} from "../../api/shapes";

export var ASPECTS: [string, string][] = [
  ["", "overview"],
  ["flows", "flows"],
  ["machines", "machines"],
  ["power", "power"],
  ["nodes", "nodes"],
  ["links", "links"],
  ["floors", "floors"],
  ["sites", "sites"],
];

export interface FactoryAddress {
  name: string;
  aspect: string;
}

function isAspect(id: string): boolean {
  return (
    !!id &&
    ASPECTS.some(function (a) {
      return a[0] === id;
    })
  );
}

export function factoryAddress(subject: string, known?: (name: string) => boolean): FactoryAddress {
  var cut = subject.lastIndexOf("/");
  if (cut > 0) {
    var tail = subject.slice(cut + 1);
    if (isAspect(tail) && !(known && known(subject))) return { name: subject.slice(0, cut), aspect: tail };
  }
  return { name: subject, aspect: "" };
}

export function factoryDash(name: string, aspect: string): string {
  return "factories/" + name + (aspect ? "/" + aspect : "");
}

export function factoryPinButton(name: string): HTMLButtonElement {
  return button(
    W.pin,
    function () {
      pinThis("factory", { factory: name });
    },
    { title: "pin this factory and copy its pin:N for chat", label: "pin " + name }
  );
}

export function aspectTabs(name: string, aspect: string): HTMLElement {
  return tabs2(
    ASPECTS.map(function (a) {
      return { id: a[0], label: a[1], href: hashFor(factoryDash(name, a[0])) };
    }),
    aspect,
    function (id) {
      var dash = factoryDash(name, id);
      history.pushState(null, "", hashFor(dash));
      state.dash = dash;
      render();
    },
    "factory sections"
  );
}

interface Slot<T> {
  key: string;
  epoch: number;
  busy: boolean;
  failure: unknown;
  data: T | null;
}

function slot<T>(): Slot<T> {
  return { key: "", epoch: -1, busy: false, failure: null, data: null };
}

var aspects = slot<FactoryAspectsResponse>();
var sites = slot<SitesResponse>();
var floors = slot<FactoryFloorsResponse>();

function want<T>(s: Slot<T>, name: string, ticketName: string, path: () => Promise<T>, soft?: (failure: unknown) => T | null): void {
  if (s.key === name && s.epoch === state.epoch && (s.busy || s.data || s.failure)) return;
  var ticket = latest(ticketName);
  s.key = name;
  s.epoch = state.epoch;
  s.busy = true;
  s.failure = null;
  s.data = null;
  path()
    .then(function (data) {
      if (!ticket.fresh()) return;
      s.data = data;
    })
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      var kept = soft ? soft(failure) : null;
      if (kept) s.data = kept;
      else s.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      s.busy = false;
      render();
    });
}

function forget<T>(s: Slot<T>): void {
  s.key = "";
  s.epoch = -1;
  s.busy = false;
  s.failure = null;
  s.data = null;
}

function ready<T>(body: HTMLElement, s: Slot<T>, what: string, retry: () => void): T | null {
  if (s.failure) {
    error(body, what, s.failure, function () {
      forget(s);
      retry();
    });
    return null;
  }
  if (!s.data) {
    loading(body, what);
    return null;
  }
  return s.data;
}

function loadAspects(name: string): void {
  want(aspects, name, "factory-aspects", function () {
    return get<FactoryAspectsResponse>(("/api/factories/aspects?factory=" + encodeURIComponent(name)) as `/api/factories/aspects?${string}`);
  });
}

function loadSites(name: string): void {
  want(sites, name, "factory-sites", function () {
    return get<SitesResponse>(("/api/factories/sites?factory=" + encodeURIComponent(name)) as `/api/factories/sites?${string}`);
  });
}

function loadFloors(name: string): void {
  want(
    floors,
    name,
    "factory-floors",
    function () {
      return get<FactoryFloorsResponse>(("/api/floors?factory=" + encodeURIComponent("label:" + name)) as `/api/floors?${string}`);
    },
    function (failure) {
      var status = (failure as { status?: number }).status;
      if (status !== 404) return null;
      return { platforms: [], note: String((failure as Error).message || "") } as unknown as FactoryFloorsResponse;
    }
  );
}

function card(parent: HTMLElement, title: string): HTMLElement {
  var section = make("section", "dash-card");
  heading(section, title);
  parent.appendChild(section);
  return section;
}

function net(value: number): string {
  return signed(value, function (magnitude) {
    return perMin(magnitude, false);
  });
}

var flowSort: SortState = { key: "net", desc: true };
var machineSort: SortState = { key: "building", desc: false };

function renderFlows(body: HTMLElement, data: FactoryAspectsResponse): void {
  var rows = data.balance;
  var tiles = make("div", "dash-tiles");
  var groups: [string, string][] = [
    ["surplus", "items that leave or back up"],
    ["needs feeding", "items fed in from outside"],
    ["internal", "items made and used inside"],
  ];
  groups.forEach(function (g) {
    var n = rows.filter(function (r) {
      return r.verdict === g[0];
    }).length;
    tiles.appendChild(tile(g[0], count(n), g[1]));
  });
  body.appendChild(tiles);
  var section = card(body, "balance per item");
  if (!rows.length) {
    empty(section, "no item flows", "nothing here has a recipe set, or every machine is paused");
    return;
  }
  section.appendChild(
    table<AspectBalance>(
      [
        {
          key: "item",
          label: "item",
          sort: function (r) {
            return r.item;
          },
          render: function (r) {
            return r.item;
          },
        },
        {
          key: "made",
          label: "made /min",
          align: "right",
          sort: function (r) {
            return r.made;
          },
          render: function (r) {
            return r.made ? perMin(r.made, false) : "–";
          },
        },
        {
          key: "used",
          label: "used /min",
          align: "right",
          sort: function (r) {
            return r.used;
          },
          render: function (r) {
            return r.used ? perMin(r.used, false) : "–";
          },
        },
        {
          key: "net",
          label: "net",
          align: "right",
          title: "made minus used at the saved clocks (nameplate)",
          sort: function (r) {
            return r.net;
          },
          render: function (r) {
            return net(r.net);
          },
        },
        {
          key: "measured",
          label: "net, measured",
          align: "right",
          title: "each machine's rate scaled by the share of its last ~300 s it spent producing; – where no machine keeps a monitor",
          sort: function (r) {
            return r.measured_net === null ? -Infinity : r.measured_net;
          },
          render: function (r) {
            return r.measured_net === null ? "–" : net(r.measured_net);
          },
        },
        {
          key: "verdict",
          label: "",
          sort: function (r) {
            return r.verdict;
          },
          render: function (r) {
            return r.verdict;
          },
        },
      ],
      rows,
      { sort: flowSort, caption: "balance per item" }
    )
  );
  note(
    section,
    count(data.producing_now) + " of " + count(data.producers) + " producing machines were mid-cycle when the save was written" +
      (data.unmonitored_producers ? " · " + counted(data.unmonitored_producers, "machine") + (data.unmonitored_producers === 1 ? " keeps" : " keep") + " no monitor, so measured is a floor" : "")
  );
}

function countTable(parent: HTMLElement, title: string, rows: AspectCount[], what: string): void {
  var section = card(parent, title);
  if (!rows.length) {
    empty(section, "no " + what);
    return;
  }
  section.appendChild(
    table<AspectCount>(
      [
        {
          key: "name",
          label: what,
          render: function (r) {
            return r.name;
          },
        },
        {
          key: "count",
          label: "machines",
          align: "right",
          render: function (r) {
            return count(r.count);
          },
        },
      ],
      rows,
      { caption: title }
    )
  );
}

function renderMachines(body: HTMLElement, data: FactoryAspectsResponse): void {
  var split = make("div", "dash-split");
  countTable(split, "buildings", data.buildings, "building");
  countTable(split, "recipes", data.recipes, "recipe");
  body.appendChild(split);
  if (data.issues.length) {
    var issues = card(body, "issues");
    var list = make("ul", "dash-list");
    data.issues.forEach(function (issue) {
      var li = make("li", "", issue.text + " ");
      if (issue.machine) {
        var copy = make("span");
        copy.innerHTML = code("machine:" + issue.machine, "copy id").html;
        li.appendChild(copy.firstChild!);
      }
      list.appendChild(li);
    });
    issues.appendChild(list);
  }
  var section = card(body, "machines");
  section.appendChild(
    table<AspectMachine>(
      [
        {
          key: "building",
          label: "building",
          sort: function (r) {
            return r.building;
          },
          render: function (r) {
            return r.building;
          },
        },
        {
          key: "recipe",
          label: "recipe",
          sort: function (r) {
            return r.recipe || "";
          },
          render: function (r) {
            return r.recipe || "–";
          },
        },
        {
          key: "clock",
          label: "clock",
          align: "right",
          sort: function (r) {
            return r.clock;
          },
          render: function (r) {
            return pct(r.clock);
          },
        },
        {
          key: "state",
          label: "",
          render: function (r) {
            return r.paused ? chip(W.paused, "mid") : "";
          },
        },
        {
          key: "map",
          label: "",
          render: function (r) {
            return pointButton({ x_m: r.x_m, y_m: r.y_m, instance: r.instance, name: r.building }, "show this " + r.building + " on the map");
          },
        },
      ],
      data.machines,
      { sort: machineSort, caption: "machines" }
    )
  );
}

function renderPowerAspect(body: HTMLElement, data: FactoryAspectsResponse): void {
  var p = data.power;
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile(W.measuredDraw, mw(p.measured_draw_mw), mw(p.draw_mw) + " nameplate"));
  tiles.appendChild(tile(W.nameplateDraw, mw(p.draw_mw), "every machine at its saved clock"));
  tiles.appendChild(tile(W.generation, mw(p.generation_mw), p.generation_mw ? "generators in this factory" : "no generators here"));
  var net = p.generation_mw - p.measured_draw_mw;
  tiles.appendChild(tile("net, measured", mw(net, { signed: true }), mw(p.generation_mw - p.draw_mw, { signed: true }) + " at nameplate", net < 0 && p.generation_mw > 0));
  body.appendChild(tiles);
  if (p.unmonitored) note(body, counted(p.unmonitored, "machine") + (p.unmonitored === 1 ? " keeps no monitor and is" : " keep no monitor and are") + " charged in full in measured draw");
  note(body, "a factory drawing from a shared grid reads negative here by design; the grid's headroom is on the Power tab");
}

function renderNodes(body: HTMLElement, data: FactoryAspectsResponse): void {
  var section = card(body, "resource nodes");
  if (!data.nodes.length) {
    empty(section, "no extractors in this factory", "it is fed from outside, or its extractors are named as another factory");
    return;
  }
  section.appendChild(
    table<AspectNode>(
      [
        {
          key: "resource",
          label: "resource",
          render: function (r) {
            return r.resource;
          },
        },
        {
          key: "purity",
          label: "purity",
          render: function (r) {
            return r.purity;
          },
        },
        {
          key: "extractor",
          label: "extractor",
          render: function (r) {
            return r.extractor;
          },
        },
        {
          key: "clock",
          label: "clock",
          align: "right",
          render: function (r) {
            return pct(r.clock);
          },
        },
        {
          key: "left",
          label: "left",
          align: "right",
          title: "what the save says is left in the node; – for an infinite one",
          render: function (r) {
            return r.left === null ? "–" : num(r.left, 0);
          },
        },
        {
          key: "map",
          label: "",
          render: function (r) {
            return pointButton({ x_m: r.x_m, y_m: r.y_m, name: r.resource + " node" });
          },
        },
      ],
      data.nodes,
      { caption: "resource nodes" }
    )
  );
}

function renderLinks(body: HTMLElement, data: FactoryAspectsResponse): void {
  var section = card(body, "material links");
  if (!data.links.length) {
    empty(section, "no belt or pipe leaves this factory for another machine");
    return;
  }
  section.appendChild(
    table<AspectLink>(
      [
        {
          key: "factory",
          label: "other side",
          render: function (r) {
            return r.factory ? link(factoryDash(r.factory, ""), r.factory) : "machines no factory covers";
          },
        },
        {
          key: "machines",
          label: "machines reached",
          align: "right",
          render: function (r) {
            return count(r.machines);
          },
        },
      ],
      data.links,
      { caption: "material links" }
    )
  );
  note(section, "machines reached on the far side through belts and pipes, not a count of belts; the two directions can differ");
}

function bandsTable(platform: FloorPlatform, name: string): HTMLElement {
  var bands = platform.bands.slice().reverse();
  return table<FloorBand>(
    [
      {
        key: "floor",
        label: "floor",
        render: function (b) {
          return "floor " + b.ordinal;
        },
      },
      {
        key: "top",
        label: "deck height",
        align: "right",
        render: function (b) {
          return b.top_m === null ? "–" : num(b.top_m, 1) + " m";
        },
      },
      {
        key: "area",
        label: "area",
        align: "right",
        render: function (b) {
          return num(b.area_m2, 0) + " m²";
        },
      },
      {
        key: "machines",
        label: "machines",
        align: "right",
        render: function (b) {
          return count(b.machine_count);
        },
      },
      {
        key: "minor",
        label: "",
        render: function (b) {
          return b.minor ? chip("minor", "muted", "under a quarter of the largest deck: a mezzanine or a plinth") : "";
        },
      },
      {
        key: "map",
        label: "",
        render: function (b) {
          return mapButton(
            "show this floor alone on the map",
            function () {
              enterFloors("platform=" + platform.index, name, String(b.ordinal));
            },
            "show floor " + b.ordinal + " on the map"
          );
        },
      },
    ],
    bands,
    { caption: "floors of platform " + platform.index }
  );
}

function renderFloors(body: HTMLElement, data: FactoryFloorsResponse, name: string): void {
  var platforms = data.platforms.filter(function (p) {
    return p.bands.length > 0;
  });
  if (!platforms.length) {
    empty(body, "no floors under this factory", data.note || "it stands on no foundation that has a deck");
    return;
  }
  var section = body;
  platforms.forEach(function (p) {
    section = card(body, "platform " + p.index + (p.label && p.label !== name ? " · " + p.label : ""));
    note(section, counted(p.bands.length, "floor") + " · " + num(p.area_m2, 0) + " m² over " + counted(p.cells, "tile"));
    section.appendChild(bandsTable(p, name));
  });
  note(section, "floors are recovered from foundation heights; a platform is poured foundation, and two factories on one slab share it");
}

function siteBox(s: SiteRow): [number, number, number, number] | null {
  if (s.x_m === null || s.y_m === null) return null;
  var r = Math.max(50, s.diameter_m / 2);
  return [s.x_m - r, s.y_m - r, s.x_m + r, s.y_m + r];
}

function renderSites(body: HTMLElement, data: SitesResponse): void {
  var section = card(body, "sites");
  if (!data.sites.length) {
    empty(section, "no site holds this factory's machines");
    return;
  }
  section.appendChild(
    table<SiteRow>(
      [
        {
          key: "where",
          label: "site",
          render: function (s) {
            return s.direction + " · " + s.grid;
          },
        },
        {
          key: "mine",
          label: "this factory",
          align: "right",
          title: "how many of this factory's machines stand in the site",
          render: function (s) {
            return count(s.mine);
          },
        },
        {
          key: "count",
          label: "buildings",
          align: "right",
          title: "every production building in the site, this factory's and any other's",
          render: function (s) {
            return count(s.count);
          },
        },
        {
          key: "spread",
          label: "spread",
          align: "right",
          render: function (s) {
            return num(s.diameter_m, 0) + " m";
          },
        },
        {
          key: "contents",
          label: "biggest kinds",
          render: function (s) {
            return s.buildings
              .slice(0, 3)
              .map(function (b) {
                return count(b.count) + "× " + b.name;
              })
              .join(", ");
          },
        },
        {
          key: "map",
          label: "",
          render: function (s) {
            var box = siteBox(s);
            if (!box) return make("span", "dash-muted", "–");
            var shown = box;
            return mapButton(
              "fly the map to this site and outline it",
              function () {
                showBox(shown);
              },
              "show site " + s.grid + " on the map"
            );
          },
        },
      ],
      data.sites,
      { caption: "sites" }
    )
  );
  var shared = data.sites.some(function (s) {
    return s.count > s.mine;
  });
  note(
    section,
    "a site is every production building within 300 m of another, named or not" +
      (shared ? "; a site bigger than this factory means it has grown together with its neighbours" : "") +
      " · " + counted(data.total, "site") + " in the world"
  );
}

export function renderAspect(body: HTMLElement, name: string, aspect: string): void {
  if (aspect === "floors") {
    loadFloors(name);
    var f = ready(body, floors, "the floors", function () {
      loadFloors(name);
    });
    if (f) renderFloors(body, f, name);
    return;
  }
  if (aspect === "sites") {
    loadSites(name);
    var s = ready(body, sites, "the sites", function () {
      loadSites(name);
    });
    if (s) renderSites(body, s);
    return;
  }
  loadAspects(name);
  var data = ready(body, aspects, "this factory's " + aspect, function () {
    loadAspects(name);
  });
  if (!data) return;
  if (aspect === "flows") renderFlows(body, data);
  else if (aspect === "machines") renderMachines(body, data);
  else if (aspect === "power") renderPowerAspect(body, data);
  else if (aspect === "nodes") renderNodes(body, data);
  else if (aspect === "links") renderLinks(body, data);
}
