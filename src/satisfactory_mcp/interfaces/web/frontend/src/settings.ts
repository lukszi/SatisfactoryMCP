/* Per-browser preferences, kept in localStorage and never sent to the server. The page
 * works without storage: a setting then lasts until reload. See docs/frontend_vision.md §8.6. */

interface Base {
  key: string;
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
  fallback: number;
}

export type Setting = Switch | Choice | Amount;

export var SETTINGS: Setting[] = [
  {
    kind: "switch",
    key: "spoilers",
    label: "Show upcoming milestones",
    hint: "Off: Progress shows the tiers you have started, done and not done, and hides the tiers ahead.",
    fallback: true,
  },
  {
    kind: "choice",
    key: "naming",
    label: "Factory name suggestions",
    hint: "How Detect factories words the name it offers. Every suggestion stays editable.",
    options: [
      ["short", "short: “steel pipe factory”"],
      ["product, region", "product and region: “Steel Pipe, Rocky Desert”"],
    ],
    fallback: "short",
  },
  {
    kind: "switch",
    key: "fedOnly",
    label: "Only suggest fed clusters",
    hint: "Hide clusters whose belts and pipes reach no miner, extractor or outside machine. A box filled by hand is not a source; a train, drone or truck station counts as unknown and stays.",
    fallback: true,
  },
  {
    kind: "number",
    key: "minMachines",
    label: "Minimum machines",
    hint: "Hide smaller clusters from Detect factories.",
    min: 1,
    fallback: 2,
  },
  {
    kind: "choice",
    key: "follow",
    label: "Follow chat",
    hint: "What the page does when chat solves a plan or opens one. A change chat makes to the plan you have open always shows up, whatever this says; the page never moves while you are typing.",
    options: [
      ["follow", "follow: open what chat works on"],
      ["toasts", "toasts only"],
      ["off", "off"],
    ],
    fallback: "follow",
  },
];

var STORE_KEY = "settings";

var values: Record<string, boolean | string | number> = {};

var listeners: Array<() => void> = [];

function valid(s: Setting, value: unknown): boolean {
  if (s.kind === "switch") return typeof value === "boolean";
  if (s.kind === "choice") {
    return s.options.some(function (o) {
      return o[0] === value;
    });
  }
  return typeof value === "number" && Number.isInteger(value) && value >= s.min;
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

export function onSetting(listener: () => void): void {
  listeners.push(listener);
}

recall();
