/* Settings → maps: the map types, the job card, the generate form, and the default picker.
 *
 * The registry and its jobs come from map-types.ts. A `maps` event that only moves a job
 * redraws the job card in place, so the form keeps what was typed into it; a change to the
 * list redraws the tab. Every confirm is inline, never a browser dialog.
 * docs/maps_contract.md §6. */

import { send } from "../../api/client";
import { button, chip, error, loading, selectBox } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { bytes } from "../../kit/format";
import { fetchMapRegistry, mapRegistry, onMapRegistry } from "../../app/map-types";
import { refused, submit, write } from "./maps-actions";
import { renderForm } from "./maps-form";
import { drawJobs } from "./maps-jobs";
import { renderTypes } from "./maps-types";

import type { MapInputBody, MapsResponse } from "../../api/shapes";

const INPUT_PRESET: Record<string, string> = {
  heightfield: "heightmap",
  caves: "caves",
  rocks: "rocks",
  paint: "paint",
};

let jobHost: HTMLElement | null = null;
let tabBody: HTMLElement | null = null;

/** Settings → general: the type a fresh page opens on, shared by every browser here. */
export function mapPickerRow(): HTMLElement {
  const row = make("label", "dash-setting");
  const words = make("span", "dash-setting-text");
  words.appendChild(make("span", "dash-setting-k", "default base map"));
  words.appendChild(make("span", "dash-setting-hint", "what a fresh page opens on, in every browser here; the map's own switcher still changes the view"));
  row.appendChild(words);
  const body = mapRegistry.body;
  if (!body) {
    row.appendChild(make("span", "dash-muted", mapRegistry.failed ? "map list unreadable" : "loading…"));
    if (!mapRegistry.failed) fetchMapRegistry();
    return row;
  }
  const options: [string, string][] = body.types
    .filter(function (type) {
      return type.status === "ready";
    })
    .map(function (type): [string, string] {
      return [type.id, type.title + (type.freshness.stale.length ? " (stale)" : "")];
    });
  options.push(["plain", "plain: no imagery"]);
  const value = body.default || "plain";
  const version = body.version;
  row.appendChild(
    selectBox(
      options,
      value,
      function (id) {
        write("PUT", "/api/maps/default", { id: id, version: version }).catch(refused("the default map was not changed"));
      },
      { label: "default base map" }
    )
  );
  return row;
}

function inputLine(row: MapInputBody): string {
  if (!row.present) return "not built";
  const parts = [];
  if (row.version !== null) parts.push("v" + row.version);
  if (row.cl !== null) parts.push("build " + row.cl);
  if (row.transcribed) parts.push("built " + row.transcribed);
  return parts.join(" · ");
}

function renderInputs(parent: HTMLElement, body: MapsResponse): void {
  const fold = make("details", "dash-card maps-inputs");
  fold.appendChild(make("summary", "dash-h maps-summary", "inputs"));
  body.inputs.forEach(function (row) {
    const line = make("div", "maps-input");
    line.appendChild(make("strong", "", row.name));
    line.appendChild(make("span", "dash-sub", inputLine(row)));
    const preset = INPUT_PRESET[row.name];
    if (preset)
      line.appendChild(
        button("rebuild", function () {
          submit(preset, {}, "", null);
        }, { disabled: !body.can_generate.ok, title: body.can_generate.ok ? "queue a rebuild of the " + row.name : body.can_generate.reason || "" })
      );
    else line.appendChild(make("span", "dash-sub", "extracted by the colour workflow"));
    fold.appendChild(line);
  });
  parent.appendChild(fold);
}

function setupLine(text: string): HTMLElement {
  const setup = make("div", "maps-setup");
  setup.appendChild(chip("setup", "muted"));
  setup.appendChild(make("span", "", text));
  return setup;
}

function renderStatus(parent: HTMLElement, body: MapsResponse): void {
  const card = make("section", "dash-card maps-status");
  const line = make("div", "maps-status-line");
  line.appendChild(make("span", "", body.types.length + " map types · " + bytes(body.disk.maps_bytes) + " · " + bytes(body.disk.free_bytes) + " free"));
  if (body.disk.cache_bytes) {
    line.appendChild(make("span", "dash-sub", " · raster cache " + bytes(body.disk.cache_bytes)));
    line.appendChild(
      button("clear cache", function () {
        send("DELETE", "/api/maps/cache")
          .then(function () {
            fetchMapRegistry();
          })
          .catch(refused("the cache was not cleared"));
      }, { title: "delete the rasters kept for fast re-renders" })
    );
  }
  card.appendChild(line);
  if (!body.can_generate.ok) {
    card.appendChild(setupLine(" " + (body.can_generate.reason || "generation cannot run here") + "; picking and deleting still work"));
  } else if (!body.can_generate.heightfield) {
    const heightfield = setupLine(" render maps need the heightfield first ");
    heightfield.appendChild(
      button("build it", function () {
        submit("heightmap", {}, "", null);
      })
    );
    card.appendChild(heightfield);
  }
  if (body.unregistered.length) {
    const found = make("div", "maps-setup");
    found.appendChild(make("span", "", "found " + body.unregistered.length + " unregistered pyramid" + (body.unregistered.length > 1 ? "s" : "") + ": " + body.unregistered.join(", ") + " "));
    found.appendChild(
      button("add", function () {
        write("POST", "/api/maps/adopt").catch(refused("nothing was added"));
      })
    );
    card.appendChild(found);
  }
  parent.appendChild(card);
}

export function renderMaps(body: HTMLElement): void {
  tabBody = body;
  const data = mapRegistry.body;
  if (!data) {
    if (mapRegistry.failed) error(body, "the map list", mapRegistry.failed, function () {
      fetchMapRegistry();
    });
    else {
      loading(body, "the map list");
      fetchMapRegistry();
    }
    return;
  }
  renderStatus(body, data);
  const jobs = make("section", "dash-card maps-jobs");
  jobs.setAttribute("aria-live", "polite");
  drawJobs(jobs);
  jobHost = jobs;
  body.appendChild(jobs);
  renderForm(body, data);
  renderTypes(body, data);
  renderInputs(body, data);
}

onMapRegistry(function (listed) {
  if (listed || !jobHost || !jobHost.isConnected || !tabBody?.isConnected) return;
  drawJobs(jobHost);
});
