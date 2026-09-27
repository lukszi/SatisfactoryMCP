/* The dashboard's Power tab: the world ledger, every circuit and the machines without power,
 * addressed as `dash=power[/<circuit>]`. */

import { cell, error, heading, link, loading, note, tile } from "./dashkit";
import { make } from "./dom";
import { mw } from "./format";
import { loadOne } from "./load";
import { showCircuit, vitals } from "./panel";
import { go, mapButton, pointButton, table } from "./dashboard";

import type { CircuitRow, Ledger, MachineRef, StarvedGenerator } from "./api-shapes";

export function powerBar(ledger: Ledger): HTMLElement {
  var track = make("div", "panel-bar dash-bar");
  var cap = Math.max(ledger.generation_mw, ledger.draw_mw, 1);
  var nameplate = make("span", "panel-bar-nameplate");
  nameplate.style.width = Math.min(100, (ledger.draw_mw / cap) * 100) + "%";
  var measured = make("span", "panel-bar-measured");
  measured.style.width = Math.min(100, (ledger.measured_draw_mw / cap) * 100) + "%";
  var generation = make("span", "panel-bar-cap");
  generation.style.left = Math.min(100, (ledger.generation_mw / cap) * 100) + "%";
  track.appendChild(nameplate);
  track.appendChild(measured);
  track.appendChild(generation);
  track.title =
    mw(ledger.measured_draw_mw) +
    " measured draw, " +
    mw(ledger.draw_mw) +
    " nameplate, against " +
    mw(ledger.generation_mw) +
    " generation";
  return track;
}

function signed(value: number): string {
  return (value > 0 ? "+" : "") + mw(value);
}

export function headroomTiles(ledger: Ledger, href?: string, counts?: boolean): HTMLElement[] {
  return [
    tile(
      "headroom now",
      signed(ledger.measured_headroom_mw),
      mw(ledger.measured_draw_mw) + " measured draw" + (counts ? " · " + ledger.monitored + " machines measured" : ""),
      ledger.measured_headroom_mw < 0,
      href
    ),
    tile(
      "headroom at full rate",
      signed(ledger.headroom_mw),
      mw(ledger.draw_mw) + " nameplate draw" + (counts ? " · " + ledger.unmonitored + " unmeasured, charged in full" : ""),
      ledger.headroom_mw < 0,
      href
    ),
  ];
}

function circuitName(row: CircuitRow): string {
  return row.factories.length ? row.factories.join(", ") : "circuit " + (row.index + 1);
}

function circuitDark(row: CircuitRow): boolean {
  return row.ledger.generation_mw <= 0 && row.consumers > 0;
}

export function circuitTable(parent: HTMLElement, rows: CircuitRow[]): void {
  var t = table(
    [
      ["circuit", "name"],
      ["generation", ""],
      ["draw, measured", ""],
      ["headroom now", ""],
      ["headroom at full rate", ""],
      ["consumers", ""],
      ["", ""],
      ["", ""],
    ],
    false
  );
  var tb = t.tBodies[0]!;
  rows.forEach(function (r) {
    var led = r.ledger;
    var tr = make("tr", "go");
    cell(tr, link("power/" + (r.index + 1), circuitName(r)));
    cell(tr, circuitDark(r) ? "no generator" : mw(led.generation_mw), "num" + (circuitDark(r) ? " bad" : ""));
    cell(tr, mw(led.measured_draw_mw), "num");
    cell(tr, signed(led.measured_headroom_mw), "num" + (led.measured_headroom_mw < 0 ? " bad" : ""));
    cell(tr, signed(led.headroom_mw), "num" + (led.headroom_mw < 0 ? " bad" : ""));
    cell(tr, r.consumers, "num");
    cell(tr, powerBar(led), "bar");
    cell(
      tr,
      r.bbox_m
        ? mapButton("fly the map to this circuit", function () {
            showCircuit(r.index);
          })
        : make("span", "dash-muted", "–"),
      "num"
    );
    tr.onclick = function () {
      go("power/" + (r.index + 1));
    };
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  parent.appendChild(wrap);
}

function ledgerTiles(ledger: Ledger): HTMLElement {
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(
    tile(
      "generation",
      mw(ledger.generation_mw),
      ledger.starved_generation_mw ? mw(ledger.starved_generation_mw) + " of it starved" : "none of it starved",
      ledger.starved_generation_mw > 0
    )
  );
  headroomTiles(ledger, undefined, true).forEach(function (t) {
    tiles.appendChild(t);
  });
  return tiles;
}

function refList(parent: HTMLElement, title: string, rows: MachineRef[], hint: string): void {
  if (!rows.length) return;
  var card = make("section", "dash-card");
  heading(card, title + " (" + rows.length + ")");
  note(card, hint);
  var list = make("ul", "dash-list");
  rows.forEach(function (m) {
    var li = make("li", "dash-issue");
    li.appendChild(make("span", "dash-what", m.name));
    li.appendChild(make("span", "dash-where", m.instance));
    li.appendChild(pointButton(m));
    list.appendChild(li);
  });
  card.appendChild(list);
  parent.appendChild(card);
}

function starvedList(parent: HTMLElement, rows: StarvedGenerator[]): void {
  if (!rows.length) return;
  var card = make("section", "dash-card");
  heading(card, "starved generators (" + rows.length + ")");
  note(card, "input ran dry and produced nothing in its window");
  var list = make("ul", "dash-list");
  rows.forEach(function (g) {
    var li = make("li", "dash-issue");
    li.appendChild(make("span", "dash-what", g.name));
    li.appendChild(make("span", "dash-where", mw(g.mw) + " · out of " + g.missing.join(", ")));
    li.appendChild(pointButton(g));
    list.appendChild(li);
  });
  card.appendChild(list);
  parent.appendChild(card);
}

export function renderPower(body: HTMLElement): void {
  var v = vitals();
  var data = v.circuits;
  if (!data) {
    if (v.circuitsError) {
      error(body, "power circuits", null, function () {
        loadOne("/api/power/circuits");
      });
    } else loading(body, "power circuits");
    return;
  }
  heading(body, "whole world");
  body.appendChild(ledgerTiles(data.world));
  body.appendChild(powerBar(data.world));
  var facts: string[] = [];
  if (data.generators.length) {
    facts.push(
      data.generators
        .map(function (g) {
          return g.count + "× " + g.name + " " + mw(g.mw);
        })
        .join(" · ")
    );
  }
  if (data.paused) facts.push(data.paused + " paused buildings are left out of both sides");
  if (data.unmodellable.length) facts.push("not in game data, left out: " + data.unmodellable.join(", "));
  facts.forEach(function (f) {
    note(body, f);
  });
  var card = make("section", "dash-card");
  heading(card, data.circuits.length + (data.circuits.length === 1 ? " circuit" : " circuits"));
  circuitTable(card, data.circuits);
  note(card, "a circuit is what the wires join; switches read as closed, batteries are not counted");
  body.appendChild(card);
  var split = make("div", "dash-split");
  starvedList(split, data.starved);
  refList(split, "no power connection", data.unwired, "machines with no wire at all");
  refList(split, "no generator on its circuit", data.no_generator, "wired, but to a circuit no generator stands on");
  body.appendChild(split);
}

export function renderCircuit(body: HTMLElement, subject: string): void {
  var v = vitals();
  if (!v.circuits) {
    note(body, v.circuitsError || "loading…");
    return;
  }
  body.appendChild(link("power", "‹ all circuits", "dash-back"));
  var row = v.circuits.circuits[+subject - 1];
  if (!row) {
    note(body, "this save has no circuit " + subject);
    return;
  }
  var head = make("div", "dash-title");
  head.appendChild(make("h1", "", "circuit " + (row.index + 1) + (row.factories.length ? ": " + circuitName(row) : "")));
  if (row.bbox_m) {
    head.appendChild(
      mapButton("fly the map to this circuit", function () {
        showCircuit(row!.index);
      })
    );
  }
  body.appendChild(head);
  note(body, row.consumers + " consumers · " + row.poles + " poles and towers");
  body.appendChild(ledgerTiles(row.ledger));
  body.appendChild(powerBar(row.ledger));
  if (row.generators.length) {
    note(
      body,
      row.generators
        .map(function (g) {
          return g.count + "× " + g.name + " " + mw(g.mw);
        })
        .join(" · ")
    );
  }
  starvedList(body, row.starved);
  note(body, "circuit numbers follow size in this save and can change when the next save is read");
}
