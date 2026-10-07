/* The factory detail page's level-2 tabs past the overview: flows, machines, power, nodes,
 * links, floors and sites. The address is `factories/<name>/<aspect>`. */

import { get, isNotFound, latest } from "../../api/client";
import { appendNote, chip, empty, error, heading, link, loading, table, tile } from "../../kit/dashkit";
import { code, make } from "../../kit/dom";
import { buildingCounts, count, formatNumber, mw, pct, perMin, signed } from "../../kit/format";
import { enterFloors } from "../../map/floors/floors";
import { showBox } from "../../map/map-highlight";
import { state } from "../../app/state";
import { counted, WORDS } from "../../kit/words";
import { mapButton, pointButton, requestRender } from "../actions";
import { factoryDash } from "./address";

import type { SortState } from "../../kit/dashkit";
import type { BboxM } from "../../map/geometry";
import type {
  AspectBalance,
  AspectCount,
  AspectLink,
  AspectMachine,
  AspectNode,
  FactoryAspectsResponse,
  FloorBand,
  FloorPlatform,
  FloorsResponse,
  SiteRow,
  SitesResponse,
} from "../../api/shapes";

interface FetchSlot<T> {
  key: string;
  epoch: number;
  busy: boolean;
  failure: unknown;
  data: T | null;
}

function emptySlot<T>(): FetchSlot<T> {
  return { key: "", epoch: -1, busy: false, failure: null, data: null };
}

const aspects = emptySlot<FactoryAspectsResponse>();
const sites = emptySlot<SitesResponse>();
const floors = emptySlot<FloorsResponse>();

const flowSort: SortState = { key: "net", desc: true };
const machineSort: SortState = { key: "building", desc: false };

/* One read per factory and save; `recover` turns a failure into data when it means "none". */
function ensureLoaded<T>(
  slot: FetchSlot<T>,
  key: string,
  ticketName: string,
  fetcher: () => Promise<T>,
  recover?: (failure: unknown) => T | null
): void {
  if (slot.key === key && slot.epoch === state.epoch && (slot.busy || slot.data || slot.failure)) return;
  const ticket = latest(ticketName);
  slot.key = key;
  slot.epoch = state.epoch;
  slot.busy = true;
  slot.failure = null;
  slot.data = null;
  fetcher()
    .then(function (data) {
      if (!ticket.fresh()) return;
      slot.data = data;
    })
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      const kept = recover ? recover(failure) : null;
      if (kept) slot.data = kept;
      else slot.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      slot.busy = false;
      requestRender();
    });
}

function resetSlot<T>(slot: FetchSlot<T>): void {
  slot.key = "";
  slot.epoch = -1;
  slot.busy = false;
  slot.failure = null;
  slot.data = null;
}

function slotDataOrPlaceholder<T>(body: HTMLElement, slot: FetchSlot<T>, what: string, retry: () => void): T | null {
  if (slot.failure) {
    error(body, what, slot.failure, function () {
      resetSlot(slot);
      retry();
    });
    return null;
  }
  if (!slot.data) {
    loading(body, what);
    return null;
  }
  return slot.data;
}

function loadAspects(name: string): void {
  ensureLoaded(aspects, name, "factory-aspects", function () {
    return get<FactoryAspectsResponse>(("/api/factories/aspects?factory=" + encodeURIComponent(name)) as `/api/factories/aspects?${string}`);
  });
}

function loadSites(name: string): void {
  ensureLoaded(sites, name, "factory-sites", function () {
    return get<SitesResponse>(("/api/factories/sites?factory=" + encodeURIComponent(name)) as `/api/factories/sites?${string}`);
  });
}

function loadFloors(name: string): void {
  ensureLoaded(
    floors,
    name,
    "factory-floors",
    function () {
      return get<FloorsResponse>(("/api/floors?factory=" + encodeURIComponent("label:" + name)) as `/api/floors?${string}`);
    },
    function (failure) {
      if (!isNotFound(failure)) return null;
      return { platforms: [], note: String((failure as Error).message || "") } as unknown as FloorsResponse;
    }
  );
}

function card(parent: HTMLElement, title: string): HTMLElement {
  const section = make("section", "dash-card");
  heading(section, title);
  parent.appendChild(section);
  return section;
}

function net(value: number): string {
  return signed(value, function (magnitude) {
    return perMin(magnitude, false);
  });
}

function renderFlows(body: HTMLElement, data: FactoryAspectsResponse): void {
  const rows = data.balance;
  const tiles = make("div", "dash-tiles");
  const verdicts: [string, string][] = [
    ["surplus", "items that leave or back up"],
    ["needs feeding", "items fed in from outside"],
    ["internal", "items made and used inside"],
  ];
  verdicts.forEach(function (verdict) {
    const items = rows.filter(function (row) {
      return row.verdict === verdict[0];
    }).length;
    tiles.appendChild(tile(verdict[0], count(items), verdict[1]));
  });
  body.appendChild(tiles);
  const section = card(body, "balance per item");
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
          sort: function (row) {
            return row.item;
          },
          render: function (row) {
            return row.item;
          },
        },
        {
          key: "made",
          label: "made /min",
          align: "right",
          sort: function (row) {
            return row.made;
          },
          render: function (row) {
            return row.made ? perMin(row.made, false) : "–";
          },
        },
        {
          key: "used",
          label: "used /min",
          align: "right",
          sort: function (row) {
            return row.used;
          },
          render: function (row) {
            return row.used ? perMin(row.used, false) : "–";
          },
        },
        {
          key: "net",
          label: "net",
          align: "right",
          title: "made minus used at the saved clocks (nameplate)",
          sort: function (row) {
            return row.net;
          },
          render: function (row) {
            return net(row.net);
          },
        },
        {
          key: "measured",
          label: "net, measured",
          align: "right",
          title: "each machine's rate scaled by the share of its last ~300 s it spent producing; – where no machine keeps a monitor",
          sort: function (row) {
            return row.measured_net === null ? -Infinity : row.measured_net;
          },
          render: function (row) {
            return row.measured_net === null ? "–" : net(row.measured_net);
          },
        },
        {
          key: "verdict",
          label: "",
          sort: function (row) {
            return row.verdict;
          },
          render: function (row) {
            return row.verdict;
          },
        },
      ],
      rows,
      { sort: flowSort, caption: "balance per item" }
    )
  );
  appendNote(
    section,
    count(data.producing_now) + " of " + count(data.producers) + " producing machines were mid-cycle when the save was written" +
      unmonitoredNote(data.unmonitored_producers)
  );
}

function unmonitoredNote(unmonitored: number): string {
  if (!unmonitored) return "";
  return " · " + counted(unmonitored, "machine") + (unmonitored === 1 ? " keeps" : " keep") + " no monitor, so measured is a floor";
}

function countTable(parent: HTMLElement, title: string, rows: AspectCount[], what: string): void {
  const section = card(parent, title);
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
          render: function (row) {
            return row.name;
          },
        },
        {
          key: "count",
          label: "machines",
          align: "right",
          render: function (row) {
            return count(row.count);
          },
        },
      ],
      rows,
      { caption: title }
    )
  );
}

function renderMachines(body: HTMLElement, data: FactoryAspectsResponse): void {
  const split = make("div", "dash-split");
  countTable(split, "buildings", data.buildings, "building");
  countTable(split, "recipes", data.recipes, "recipe");
  body.appendChild(split);
  if (data.issues.length) {
    const issues = card(body, "issues");
    const list = make("ul", "dash-list");
    data.issues.forEach(function (issue) {
      const item = make("li", "", issue.text + " ");
      if (issue.machine) {
        const copy = make("span");
        copy.innerHTML = code("machine:" + issue.machine, "copy id").html;
        item.appendChild(copy.firstChild!);
      }
      list.appendChild(item);
    });
    issues.appendChild(list);
  }
  const section = card(body, "machines");
  section.appendChild(
    table<AspectMachine>(
      [
        {
          key: "building",
          label: "building",
          sort: function (machine) {
            return machine.building;
          },
          render: function (machine) {
            return machine.building;
          },
        },
        {
          key: "recipe",
          label: "recipe",
          sort: function (machine) {
            return machine.recipe || "";
          },
          render: function (machine) {
            return machine.recipe || "–";
          },
        },
        {
          key: "clock",
          label: "clock",
          align: "right",
          sort: function (machine) {
            return machine.clock;
          },
          render: function (machine) {
            return pct(machine.clock);
          },
        },
        {
          key: "state",
          label: "",
          render: function (machine) {
            return machine.paused ? chip(WORDS.paused, "mid") : "";
          },
        },
        {
          key: "map",
          label: "",
          render: function (machine) {
            return pointButton(
              { x_m: machine.x_m, y_m: machine.y_m, instance: machine.instance, name: machine.building },
              "show this " + machine.building + " on the map"
            );
          },
        },
      ],
      data.machines,
      { sort: machineSort, caption: "machines" }
    )
  );
}

function renderPowerAspect(body: HTMLElement, data: FactoryAspectsResponse): void {
  const power = data.power;
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile(WORDS.measuredDraw, mw(power.measured_draw_mw), mw(power.draw_mw) + " nameplate"));
  tiles.appendChild(tile(WORDS.nameplateDraw, mw(power.draw_mw), "every machine at its saved clock"));
  tiles.appendChild(tile(WORDS.generation, mw(power.generation_mw), power.generation_mw ? "generators in this factory" : "no generators here"));
  const measuredNet = power.generation_mw - power.measured_draw_mw;
  tiles.appendChild(
    tile(
      "net, measured",
      mw(measuredNet, { signed: true }),
      mw(power.generation_mw - power.draw_mw, { signed: true }) + " at nameplate",
      measuredNet < 0 && power.generation_mw > 0
    )
  );
  body.appendChild(tiles);
  if (power.unmonitored) {
    appendNote(body, counted(power.unmonitored, "machine") + (power.unmonitored === 1 ? " keeps no monitor and is" : " keep no monitor and are") + " charged in full in measured draw");
  }
  appendNote(body, "a factory drawing from a shared grid reads negative here by design; the grid's headroom is on the Power tab");
}

function renderNodes(body: HTMLElement, data: FactoryAspectsResponse): void {
  const section = card(body, "resource nodes");
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
          render: function (node) {
            return node.resource;
          },
        },
        {
          key: "purity",
          label: "purity",
          render: function (node) {
            return node.purity;
          },
        },
        {
          key: "extractor",
          label: "extractor",
          render: function (node) {
            return node.extractor;
          },
        },
        {
          key: "clock",
          label: "clock",
          align: "right",
          render: function (node) {
            return pct(node.clock);
          },
        },
        {
          key: "left",
          label: "left",
          align: "right",
          title: "what the save says is left in the node; – for an infinite one",
          render: function (node) {
            return node.left === null ? "–" : formatNumber(node.left, 0);
          },
        },
        {
          key: "map",
          label: "",
          render: function (node) {
            return pointButton({ x_m: node.x_m, y_m: node.y_m, name: node.resource + " node" });
          },
        },
      ],
      data.nodes,
      { caption: "resource nodes" }
    )
  );
}

function renderLinks(body: HTMLElement, data: FactoryAspectsResponse): void {
  const section = card(body, "material links");
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
          render: function (other) {
            return other.factory ? link(factoryDash(other.factory, ""), other.factory) : "machines no factory covers";
          },
        },
        {
          key: "machines",
          label: "machines reached",
          align: "right",
          render: function (other) {
            return count(other.machines);
          },
        },
      ],
      data.links,
      { caption: "material links" }
    )
  );
  appendNote(section, "machines reached on the far side through belts and pipes, not a count of belts; the two directions can differ");
}

function bandsTable(platform: FloorPlatform, name: string): HTMLElement {
  const bands = platform.bands.slice().reverse();
  return table<FloorBand>(
    [
      {
        key: "floor",
        label: "floor",
        render: function (band) {
          return "floor " + band.ordinal;
        },
      },
      {
        key: "top",
        label: "deck height",
        align: "right",
        render: function (band) {
          return band.top_m === null ? "–" : formatNumber(band.top_m, 1) + " m";
        },
      },
      {
        key: "area",
        label: "area",
        align: "right",
        render: function (band) {
          return formatNumber(band.area_m2, 0) + " m²";
        },
      },
      {
        key: "machines",
        label: "machines",
        align: "right",
        render: function (band) {
          return count(band.machine_count);
        },
      },
      {
        key: "minor",
        label: "",
        render: function (band) {
          return band.minor ? chip("minor", "muted", "under a quarter of the largest deck: a mezzanine or a plinth") : "";
        },
      },
      {
        key: "map",
        label: "",
        render: function (band) {
          return mapButton(
            "show this floor alone on the map",
            function () {
              enterFloors("platform=" + platform.index, name, String(band.ordinal));
            },
            "show floor " + band.ordinal + " on the map"
          );
        },
      },
    ],
    bands,
    { caption: "floors of platform " + platform.index }
  );
}

function renderFloors(body: HTMLElement, data: FloorsResponse, name: string): void {
  const platforms = data.platforms.filter(function (platform) {
    return platform.bands.length > 0;
  });
  if (!platforms.length) {
    empty(body, "no floors under this factory", data.note || "it stands on no foundation that has a deck");
    return;
  }
  let section = body;
  platforms.forEach(function (platform) {
    section = card(body, "platform " + platform.index + (platform.label && platform.label !== name ? " · " + platform.label : ""));
    appendNote(section, counted(platform.bands.length, "floor") + " · " + formatNumber(platform.area_m2, 0) + " m² over " + counted(platform.cells, "tile"));
    section.appendChild(bandsTable(platform, name));
  });
  appendNote(section, "floors are recovered from foundation heights; a platform is poured foundation, and two factories on one slab share it");
}

/* A site is drawn as a square at least 100 m across. */
function siteBox(site: SiteRow): BboxM | null {
  if (site.x_m === null || site.y_m === null) return null;
  const half = Math.max(50, site.diameter_m / 2);
  return [site.x_m - half, site.y_m - half, site.x_m + half, site.y_m + half];
}

function renderSites(body: HTMLElement, data: SitesResponse): void {
  const section = card(body, "sites");
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
          render: function (site) {
            return site.direction + " · " + site.grid;
          },
        },
        {
          key: "mine",
          label: "this factory",
          align: "right",
          title: "how many of this factory's machines stand in the site",
          render: function (site) {
            return count(site.mine);
          },
        },
        {
          key: "count",
          label: "buildings",
          align: "right",
          title: "every production building in the site, this factory's and any other's",
          render: function (site) {
            return count(site.count);
          },
        },
        {
          key: "spread",
          label: "spread",
          align: "right",
          render: function (site) {
            return formatNumber(site.diameter_m, 0) + " m";
          },
        },
        {
          key: "contents",
          label: "biggest kinds",
          render: function (site) {
            return buildingCounts(site.buildings, ", ", 3, count);
          },
        },
        {
          key: "map",
          label: "",
          render: function (site) {
            const box = siteBox(site);
            if (!box) return make("span", "dash-muted", "–");
            return mapButton(
              "fly the map to this site and outline it",
              function () {
                showBox(box);
              },
              "show site " + site.grid + " on the map"
            );
          },
        },
      ],
      data.sites,
      { caption: "sites" }
    )
  );
  const shared = data.sites.some(function (site) {
    return site.count > site.mine;
  });
  appendNote(
    section,
    "a site is every production building within 300 m of another, named or not" +
      (shared ? "; a site bigger than this factory means it has grown together with its neighbours" : "") +
      " · " + counted(data.total, "site") + " in the world"
  );
}

export function renderAspect(body: HTMLElement, name: string, aspect: string): void {
  if (aspect === "floors") {
    loadFloors(name);
    const floorData = slotDataOrPlaceholder(body, floors, "the floors", function () {
      loadFloors(name);
    });
    if (floorData) renderFloors(body, floorData, name);
    return;
  }
  if (aspect === "sites") {
    loadSites(name);
    const siteData = slotDataOrPlaceholder(body, sites, "the sites", function () {
      loadSites(name);
    });
    if (siteData) renderSites(body, siteData);
    return;
  }
  loadAspects(name);
  const data = slotDataOrPlaceholder(body, aspects, "this factory's " + aspect, function () {
    loadAspects(name);
  });
  if (!data) return;
  if (aspect === "flows") renderFlows(body, data);
  else if (aspect === "machines") renderMachines(body, data);
  else if (aspect === "power") renderPowerAspect(body, data);
  else if (aspect === "nodes") renderNodes(body, data);
  else if (aspect === "links") renderLinks(body, data);
}
