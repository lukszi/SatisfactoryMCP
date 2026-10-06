/* The settings the page shares with chat: read from /api/settings at boot, written on change,
 * followed through the `settings` event. A browser's own old value is pushed once, only
 * where the server has none. See docs/shared-settings.md. */

import { get, send } from "../api/client";
import { adoptShared, sharedLocal, writeSharedWith } from "./settings";
import { fail, friendlyError, notify } from "../kit/toast";

import type { ApiPath, StatusError } from "../api/client";
import type { SettingsResponse, SettingsStaleResponse } from "../api/shapes";

var SETTINGS_PATH: ApiPath = "/api/settings";
var PUSHED_KEY = "shared-settings-pushed";

var version: number | null = null;

function adoptServerSettings(body: SettingsResponse): void {
  if (version !== null && body.version < version) return;
  version = body.version;
  adoptShared(body.values);
}

/** True the first time this browser asks, and marks it asked. */
function claimFirstPush(): boolean {
  try {
    if (localStorage.getItem(PUSHED_KEY)) return false;
    localStorage.setItem(PUSHED_KEY, "1");
  } catch (ignored) {
    /* without storage the push repeats, and only_unset makes that harmless */
  }
  return true;
}

/* A stale version is retried once against the version the refusal names. */
function patchSharedSettings(values: Record<string, unknown>, retried: boolean): void {
  send<SettingsResponse>("PATCH", SETTINGS_PATH, { values: values, version: version })
    .then(adoptServerSettings)
    .catch(function (reason: StatusError) {
      var stale = reason.status === 409 ? (reason.body as SettingsStaleResponse | undefined) : undefined;
      if (stale && stale.settings && !retried) {
        version = stale.settings.version;
        patchSharedSettings(values, true);
        return;
      }
      if (stale && stale.settings) adoptServerSettings(stale.settings);
      fail("the shared setting was not saved: " + friendlyError(reason));
    });
}

export function refetchSharedSettings(): Promise<void> {
  return get<SettingsResponse>(SETTINGS_PATH)
    .then(adoptServerSettings)
    .catch(function (reason) {
      fail("shared settings unreadable, using this browser's: " + friendlyError(reason));
    });
}

export function syncSharedSettings(): Promise<void> {
  writeSharedWith(function (changes) {
    patchSharedSettings(changes, false);
  });
  var local = claimFirstPush() ? sharedLocal() : {};
  return get<SettingsResponse>(SETTINGS_PATH)
    .then(function (body) {
      var adopt: Record<string, unknown> = {};
      Object.keys(local).forEach(function (key) {
        if (body.stored.indexOf(key) < 0) adopt[key] = local[key];
      });
      if (!Object.keys(adopt).length) return adoptServerSettings(body);
      return send<SettingsResponse>("PATCH", SETTINGS_PATH, { values: adopt, only_unset: true }).then(adoptServerSettings);
    })
    .catch(function (reason) {
      fail("shared settings unreadable, using this browser's: " + friendlyError(reason));
    });
}

export function onSettingsEvent(body: SettingsResponse): void {
  if (version !== null && body.version <= version) return;
  adoptServerSettings(body);
  if (body.by && body.by.kind !== "page") notify(body.by.display + " changed a shared setting");
}
