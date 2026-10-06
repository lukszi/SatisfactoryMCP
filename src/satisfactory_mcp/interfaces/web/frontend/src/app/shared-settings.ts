/* The settings the page shares with chat: read from /api/settings at boot, written on change,
 * followed through the `settings` event. A browser's own old value is pushed once, only
 * where the server has none. See docs/shared-settings.md. */

import { get, send } from "../api/client";
import { adoptShared, sharedLocal, writeSharedWith } from "./settings";
import { fail, friendly, note } from "../kit/toast";

import type { ApiPath, StatusError } from "../api/client";
import type { SettingsResponse, SettingsStaleResponse } from "../api/shapes";

var SETTINGS: ApiPath = "/api/settings";
var PUSHED_KEY = "shared-settings-pushed";

var version: number | null = null;

function apply(body: SettingsResponse): void {
  if (version !== null && body.version < version) return;
  version = body.version;
  adoptShared(body.values);
}

function pushedBefore(): boolean {
  try {
    if (localStorage.getItem(PUSHED_KEY)) return true;
    localStorage.setItem(PUSHED_KEY, "1");
  } catch (ignored) {
    /* without storage the push repeats, and only_unset makes that harmless */
  }
  return false;
}

function write(values: Record<string, unknown>, retried: boolean): void {
  send<SettingsResponse>("PATCH", SETTINGS, { values: values, version: version })
    .then(apply)
    .catch(function (reason: StatusError) {
      var stale = reason.status === 409 ? (reason.body as SettingsStaleResponse | undefined) : undefined;
      if (stale && stale.settings && !retried) {
        version = stale.settings.version;
        write(values, true);
        return;
      }
      if (stale && stale.settings) apply(stale.settings);
      fail("the shared setting was not saved: " + friendly(reason));
    });
}

export function refetchSharedSettings(): Promise<void> {
  return get<SettingsResponse>(SETTINGS)
    .then(apply)
    .catch(function (reason) {
      fail("shared settings unreadable, using this browser's: " + friendly(reason));
    });
}

export function syncSharedSettings(): Promise<void> {
  writeSharedWith(function (changes) {
    write(changes, false);
  });
  var local = pushedBefore() ? {} : sharedLocal();
  return get<SettingsResponse>(SETTINGS)
    .then(function (body) {
      var adopt: Record<string, unknown> = {};
      Object.keys(local).forEach(function (key) {
        if (body.stored.indexOf(key) < 0) adopt[key] = local[key];
      });
      if (!Object.keys(adopt).length) return apply(body);
      return send<SettingsResponse>("PATCH", SETTINGS, { values: adopt, only_unset: true }).then(apply);
    })
    .catch(function (reason) {
      fail("shared settings unreadable, using this browser's: " + friendly(reason));
    });
}

export function onSettingsEvent(body: SettingsResponse): void {
  if (version !== null && body.version <= version) return;
  apply(body);
  if (body.by && body.by.kind !== "page") note(body.by.display + " changed a shared setting");
}
