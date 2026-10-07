/* The factory detail page's level-2 tabs past the overview: flows, machines, power, nodes,
 * links, floors and sites. The address is `factories/<name>/<aspect>`. */

import { get, isNotFound, latest } from "../../api/client";
import { appendNote, chip, empty, error, link, loading, table, tile } from "../../kit/dashkit";
import { code, make } from "../../kit/dom";
import { count, formatNumber, mw, pct, perMin, signed } from "../../kit/format";
import { state } from "../../app/state";
import { counted, WORDS } from "../../kit/words";
import { pointButton, requestRender } from "../actions";
import { factoryDash } from "./address";
import { aspectCard, renderFloors, renderSites } from "./factory-places";

import type { SortState } from "../../kit/dashkit";
import type {
  AspectBalance,
  AspectCount,
  AspectLink,
  AspectMachine,
  AspectNode,
  FactoryAspectsResponse,
  FloorsResponse,
  SitesResponse,
} from "../../api/shapes";
import type { FloorsView } from "./factory-places";

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
const floors = emptySlot<FloorsView>();

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
      return { platforms: [], note: String((failure as Error).message || "") };
    }
  );
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
  const section = aspectCard(body, "balance per item");
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
  const section = aspectCard(parent, title);
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
    const issues = aspectCard(body, "issues");
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
  const section = aspectCard(body, "machines");
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
  const section = aspectCard(body, "resource nodes");
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
  const section = aspectCard(body, "material links");
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
