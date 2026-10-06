/* Per-browser preferences, kept in localStorage; a route whose answer depends on one takes it
 * as a query. The page works without storage: a setting then lasts until reload. See
 * docs/frontend_vision.md §8.6. A `shared` one lives on the server (shared-settings.ts). */

import { W } from "../kit/words";

interface Base {
  key: string;
  group: string;
  label: string;
  hint: string;
  shared?: string;
}

export interface Switch extends Base {
  kind: "switch";
  fallback: boolean;
}

export interface Choice extends Base {
  kind: "choice";
  options: [string, string][];
  fallback: string;
}

export interface Amount extends Base {
  kind: "number";
  min: number;
  max: number;
  fallback: number;
}

export type Setting = Switch | Choice | Amount;

export var SETTINGS: Setting[] = [
  {
    kind: "switch",
    key: "spoilers",
    group: "spoilers",
    label: "show what is not unlocked yet",
    hint: "later tiers, MAM trees, phases, locked recipes and unfound pickups",
    fallback: false,
  },
  {
    kind: "choice",
    key: "naming",
    group: "detect factories",
    label: "name suggestions",
    hint: "every suggestion stays editable",
    options: [
      ["short", "short: “steel pipe factory”"],
      ["product, region", "product and region: “Steel Pipe, Rocky Desert”"],
    ],
    fallback: "short",
  },
  {
    kind: "switch",
    key: "fedOnly",
    group: "detect factories",
    label: "only fed " + W.unnamedClusters,
    hint: "a belt or pipe reaches a miner, extractor or outside machine",
    fallback: true,
  },
  {
    kind: "number",
    key: "minMachines",
    group: "detect factories",
    label: "minimum machines",
    hint: "smaller " + W.unnamedClusters + " stay hidden",
    min: 1,
    max: 500,
    fallback: 2,
  },
  {
    kind: "switch",
    key: "biomass",
    group: "power",
    label: "count biomass burners in headroom",
    hint: "hand-fed, so left out of generation and headroom by default; chat uses the same",
    fallback: false,
    shared: "biomass",
  },
  {
    kind: "choice",
    key: "progress",
    group: "planner",
    label: "built progress",
    hint: "a plan's headline on Track and in the plans list; click the figure to switch",
    options: [
      ["machines", "machines: “12 / 16 machines”"],
      ["percent", "percent of the planned rate: “75%”"],
    ],
    fallback: "machines",
  },
  {
    kind: "choice",
    key: "follow",
    group: "planner",
    label: "follow chat",
    hint: "when chat solves or opens a plan, or searches the world",
    options: [
      ["follow", "open what chat works on"],
      ["toasts", "toasts only"],
      ["off", "off"],
    ],
    fallback: "follow",
  },
  {
    kind: "choice",
    key: "stageHeadroom",
    group: "planner",
    label: "stage headroom",
    hint: "for a plan with no startup headroom of its own; chat uses the same",
    options: [
      ["measured", "measured: what the grid has free now"],
      ["nameplate", "nameplate: every built machine running at once"],
    ],
    fallback: "measured",
    shared: "stage_headroom",
  },
  {
    kind: "number",
    key: "paybackHours",
    group: "planner",
    label: "payback horizon, hours",
    hint: "for a plan that sets none: extra, slower machines must repay their build in saved power within this many hours of play; 0 builds plainly; chat uses the same",
    min: 0,
    max: 100,
    fallback: 0,
    shared: "payback_hours",
  },
  {
    kind: "switch",
    key: "overclockLast",
    group: "planner",
    label: "overclock the last machine",
    hint: "for a plan that sets none: a row is built one machine short, its last machine overclocked with 1–2 Power Shards; chat uses the same",
    fallback: false,
    shared: "overclock_last",
  },
  {
    kind: "choice",
    key: "siteSnap",
    group: "planner",
    label: "pad snap",
    hint: "how a moved plan pad lands; Shift moves freely; chat uses the same",
    options: [
      ["fine", "1 m and 15° steps"],
      ["grid8", "8 m world grid, 90° steps"],
    ],
    fallback: "fine",
    shared: "site_snap",
  },
  {
    kind: "switch",
    key: "adviceBoxFed",
    group: "advisors",
    label: "note emptied hand-fed boxes",
    hint: "a machine fed only from a storage box that ran dry; off, as hand-fed boxes are temporary; chat uses the same",
    fallback: false,
    shared: "advice_box_fed",
  },
  {
    kind: "choice",
    key: "sunTime",
    group: "map",
    label: "sun",
    hint: "where the sun stands on a map drawn with live light; the sun button on the map moves it for this visit",
    options: [
      ["noon", "game noon: high, from the south-west"],
      ["09:00", "09:00: morning, from the west"],
      ["16:00", "16:00: afternoon, low from the south-south-east"],
      ["nw", "map north-west, 45° (the relief-map convention)"],
    ],
    fallback: "noon",
  },
  {
    kind: "switch",
    key: "sunShadows",
    group: "map",
    label: "cast shadows",
    hint: "rocks and cliffs shade the ground beyond them, fading out by 150 m",
    fallback: true,
  },
  {
    kind: "switch",
    key: "sunSky",
    group: "map",
    label: "sky light",
    hint: "hollows and the foot of a cliff see less sky and read darker",
    fallback: true,
  },
];

var STORE_KEY = "settings";

var NOTICE_KEY = "spoilers-off-notice";

var values: Record<string, boolean | string | number> = {};

var listeners: Array<() => void> = [];

type Changes = Record<string, boolean | string | number | null>;

var sharedWriter: ((changes: Changes) => void) | null = null;

function valid(s: Setting, value: unknown): boolean {
  if (s.kind === "switch") return typeof value === "boolean";
  if (s.kind === "choice") {
    return s.options.some(function (o) {
      return o[0] === value;
    });
  }
  return typeof value === "number" && Number.isInteger(value) && value >= s.min && value <= s.max;
}

function recall(): void {
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    SETTINGS.forEach(function (s) {
      if (valid(s, saved[s.key])) values[s.key] = saved[s.key];
    });
  } catch (ignored) {
    /* storage is a convenience */
  }
}

function remember(): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(values));
  } catch (ignored) {
    /* storage is a convenience */
  }
}

function find(key: string): Setting | undefined {
  return SETTINGS.filter(function (s) {
    return s.key === key;
  })[0];
}

function read(key: string): boolean | string | number | undefined {
  if (key in values) return values[key];
  var found = find(key);
  return found ? found.fallback : undefined;
}

export function setting(key: string): boolean {
  return read(key) === true;
}

export function spoilerFlag(): string {
  return setting("spoilers") ? "1" : "0";
}

export function spoilerQuery(): string {
  return "spoilers=" + spoilerFlag();
}

export function choice(key: string): string {
  var value = read(key);
  return typeof value === "string" ? value : "";
}

export function amount(key: string): number {
  var value = read(key);
  return typeof value === "number" ? value : 0;
}

export function setSetting(key: string, value: boolean | string | number): void {
  var found = find(key);
  if (!found || !valid(found, value)) return;
  values[key] = value;
  remember();
  listeners.forEach(function (listener) {
    listener();
  });
  if (found.shared && sharedWriter) sharedWriter({ [found.shared]: value });
}

export function resetSettings(): void {
  var cleared: Changes = {};
  SETTINGS.forEach(function (s) {
    if (s.shared) cleared[s.shared] = null;
  });
  if (sharedWriter) sharedWriter(cleared);
  values = {};
  remember();
  listeners.forEach(function (listener) {
    listener();
  });
}

export function spoilerNotice(): boolean {
  if ("spoilers" in values) return false;
  try {
    if (localStorage.getItem(NOTICE_KEY)) return false;
    localStorage.setItem(NOTICE_KEY, "1");
  } catch (ignored) {
    return false;
  }
  return true;
}

export function onSetting(listener: () => void): void {
  listeners.push(listener);
}

export function writeSharedWith(writer: (changes: Changes) => void): void {
  sharedWriter = writer;
}

/* The shared settings this browser set itself, by server name. */
export function sharedLocal(): Changes {
  var out: Changes = {};
  SETTINGS.forEach(function (s) {
    if (s.shared && s.key in values) out[s.shared] = values[s.key]!;
  });
  return out;
}

/* The server's values replace this browser's; listeners hear it only when one moved. */
export function adoptShared(server: Record<string, unknown>): void {
  var moved = false;
  SETTINGS.forEach(function (s) {
    if (!s.shared || !valid(s, server[s.shared])) return;
    var value = server[s.shared] as boolean | string | number;
    if (read(s.key) !== value) moved = true;
    values[s.key] = value;
  });
  remember();
  if (moved)
    listeners.forEach(function (listener) {
      listener();
    });
}

recall();
