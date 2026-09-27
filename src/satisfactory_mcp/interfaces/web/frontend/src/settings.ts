/* Per-browser preferences, kept in localStorage; a route whose answer depends on one takes it
 * as a query. The page works without storage: a setting then lasts until reload. See
 * docs/frontend_vision.md §8.6. */

import { W } from "./words";

interface Base {
  key: string;
  group: string;
  label: string;
  hint: string;
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
    hint: "later tiers, MAM trees, phases, locked recipes, locked nodes and unfound pickups",
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
    hint: "hand-fed, so left out of generation and headroom by default",
    fallback: false,
  },
  {
    kind: "choice",
    key: "follow",
    group: "planner",
    label: "follow chat",
    hint: "when chat solves or opens a plan",
    options: [
      ["follow", "open what chat works on"],
      ["toasts", "toasts only"],
      ["off", "off"],
    ],
    fallback: "follow",
  },
];

var STORE_KEY = "settings";

var NOTICE_KEY = "spoilers-off-notice";

var values: Record<string, boolean | string | number> = {};

var listeners: Array<() => void> = [];

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
}

export function resetSettings(): void {
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

recall();
