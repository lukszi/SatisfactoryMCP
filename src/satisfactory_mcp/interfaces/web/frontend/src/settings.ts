/* Per-browser preferences, kept in localStorage and never sent to the server. The page
 * works without storage: a setting then lasts until reload. See docs/frontend_vision.md §8.6. */

export interface Setting {
  key: string;
  label: string;
  hint: string;
  fallback: boolean;
}

export var SETTINGS: Setting[] = [
  {
    key: "spoilers",
    label: "Show upcoming milestones",
    hint: "Off: Progress shows the tiers you have started, done and not done, and hides the tiers ahead.",
    fallback: true,
  },
];

var STORE_KEY = "settings";

var values: Record<string, boolean> = {};

var listeners: Array<() => void> = [];

function recall(): void {
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    SETTINGS.forEach(function (s) {
      if (typeof saved[s.key] === "boolean") values[s.key] = saved[s.key];
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

export function setting(key: string): boolean {
  if (key in values) return values[key]!;
  var found = SETTINGS.filter(function (s) {
    return s.key === key;
  })[0];
  return found ? found.fallback : false;
}

export function setSetting(key: string, value: boolean): void {
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
