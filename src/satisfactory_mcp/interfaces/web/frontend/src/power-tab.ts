/* The dashboard's Power tab: the world ledger, every circuit and the machines without power,
 * addressed as `dash=power[/<circuit>]`. */

import { empty, error, heading, link, loading, note, table, tile } from "./dashkit";
import { make } from "./dom";
import { count, mw, regionLine } from "./format";
import { loadOne } from "./load";
import { showCircuit, vitals } from "./panel";
import { go, mapButton, pointButton } from "./dashboard";
import { bar, circuitDark, circuitName, headroom, LEDGER } from "./powerview";
import { counted, W } from "./words";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, StarvedGenerator } from "./api-shapes";

type GeneratorGroup = CircuitsResponse["generators"][number];

var NONE = "–";

var UNRATED = "generation not modelled";

var UNMEASURED = "no machine measured";

interface Rated {
  ledger: Ledger;
  unmodellable: string[];
  dark: boolean;
}

function unrated(r: Rated): boolean {
  return r.unmodellable.length > 0;
}

function unmeasured(r: Rated): boolean {
  return r.ledger.monitored === 0;
}

function rated(row: CircuitRow): Rated {
  return { ledger: row.ledger, unmodellable: row.unmodellable, dark: circuitDark(row) };
}

export function world(data: CircuitsResponse): Rated {
  return { ledger: data.world, unmodellable: [], dark: false };
}

export function retryCircuits(): void {
  loadOne("/api/power/circuits");
}

function unratedTitle(r: Rated): string {
  return "game data cannot rate " + r.unmodellable.join(", ") + ", so this circuit's output is unknown";
}

export function headroomTiles(r: Rated, href?: string, counts?: boolean): HTMLElement[] {
  var led = r.ledger;
  var now: HTMLElement;
  if (unrated(r)) now = tile(LEDGER.headroomNow, NONE, UNRATED, false, href);
  else if (unmeasured(r)) now = tile(LEDGER.headroomNow, NONE, UNMEASURED + "; see " + LEDGER.headroomFull, false, href);
  else {
    now = tile(
      LEDGER.headroomNow,
      headroom(led.measured_headroom_mw),
      mw(led.measured_draw_mw) + " " + LEDGER.measuredDraw + (counts ? " · " + counted(led.monitored, "machine") + " measured" : ""),
      led.measured_headroom_mw < 0,
      href
    );
  }
  var full = unrated(r)
    ? tile(LEDGER.headroomFull, NONE, UNRATED, false, href)
    : tile(
        LEDGER.headroomFull,
        headroom(led.headroom_mw),
        mw(led.draw_mw) + " " + LEDGER.nameplateDraw + (counts && led.unmonitored ? " · " + count(led.unmonitored) + " unmeasured, charged in full" : ""),
        led.headroom_mw < 0,
        href
      );
  if (unrated(r)) now.title = full.title = unratedTitle(r);
  return [now, full];
}

export function generationTile(r: Rated, sub: string, href?: string): HTMLElement {
  if (unrated(r)) {
    var box = tile(LEDGER.generation, NONE, UNRATED, false, href);
    box.title = unratedTitle(r);
    return box;
  }
  if (r.dark) return tile(LEDGER.generation, W.noGenerator, sub, true, href);
  return tile(LEDGER.generation, mw(r.ledger.generation_mw), sub, r.ledger.starved_generation_mw > 0, href);
}

function starvedSub(led: Ledger): string {
  return led.starved_generation_mw ? mw(led.starved_generation_mw) + " of it starved" : "none of it starved";
}

function ledgerTiles(r: Rated, sub: string): HTMLElement {
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(generationTile(r, sub));
  headroomTiles(r, undefined, true).forEach(function (t) {
    tiles.appendChild(t);
  });
  return tiles;
}

function consumers(row: CircuitRow): string {
  return counted(row.consumers, "consumer") + (row.ledger.paused ? ", " + count(row.ledger.paused) + " " + W.paused : "");
}

function toned(bad: (r: CircuitRow) => boolean): (r: CircuitRow) => string {
  return function (r) {
    return bad(r) ? "bad" : "";
  };
}

function nowUnknown(r: CircuitRow): boolean {
  return unrated(rated(r)) || unmeasured(rated(r));
}

export function circuitTable(parent: HTMLElement, rows: CircuitRow[]): void {
  parent.appendChild(
    table<CircuitRow>(
      [
        {
          key: "circuit",
          label: "circuit",
          render: function (r) {
            var a = link("power/" + (r.index + 1), circuitName(r), "dash-trunc");
            a.title = circuitName(r);
            return a;
          },
        },
        {
          key: "generation",
          label: LEDGER.generation,
          align: "right",
          tone: toned(circuitDark),
          render: function (r) {
            if (unrated(rated(r))) return NONE;
            return circuitDark(r) ? W.noGenerator : mw(r.ledger.generation_mw);
          },
        },
        {
          key: "draw",
          label: LEDGER.measuredDraw,
          align: "right",
          render: function (r) {
            return unmeasured(rated(r)) ? NONE : mw(r.ledger.measured_draw_mw);
          },
        },
        {
          key: "now",
          label: LEDGER.headroomNow,
          align: "right",
          tone: toned(function (r) {
            return !nowUnknown(r) && r.ledger.measured_headroom_mw < 0;
          }),
          render: function (r) {
            return nowUnknown(r) ? NONE : headroom(r.ledger.measured_headroom_mw);
          },
        },
        {
          key: "full",
          label: LEDGER.headroomFull,
          align: "right",
          tone: toned(function (r) {
            return !unrated(rated(r)) && r.ledger.headroom_mw < 0;
          }),
          render: function (r) {
            return unrated(rated(r)) ? NONE : headroom(r.ledger.headroom_mw);
          },
        },
        {
          key: "consumers",
          label: "consumers",
          align: "right",
          title: "every machine on the circuit, paused ones too",
          render: function (r) {
            return count(r.consumers);
          },
        },
        {
          key: "bar",
          label: "",
          className: "bar",
          render: function (r) {
            return bar(r.ledger);
          },
        },
        {
          key: "map",
          label: "",
          align: "right",
          render: function (r) {
            return r.bbox_m
              ? mapButton(
                  "fly the map to this circuit",
                  function () {
                    showCircuit(r.index);
                  },
                  "show circuit " + (r.index + 1) + " on the map"
                )
              : make("span", "dash-muted", NONE);
          },
        },
      ],
      rows,
      {
        onRow: function (r) {
          go("power/" + (r.index + 1));
        },
        rowTitle: function (r) {
          if (unrated(rated(r))) return unratedTitle(rated(r));
          if (unmeasured(rated(r))) return UNMEASURED + " on this circuit";
          return r.ledger.paused ? consumers(r) : "";
        },
        caption: "power per circuit",
      }
    )
  );
}

interface Problem {
  problem: string;
  what: string;
  where: string;
  whereDash: string;
  cause: string;
  refs: { instance: string; x_m: number | null; y_m: number | null }[];
}

function whereOf(m: MachineRef): { where: string; dash: string } {
  if (m.factory) return { where: m.factory, dash: "factories/" + m.factory };
  if (m.circuit !== null) return { where: "circuit " + (m.circuit + 1), dash: "power/" + (m.circuit + 1) };
  return { where: m.region ? regionLine(m.region) : "", dash: "" };
}

function whereCell(p: { where: string; whereDash: string }): string | HTMLElement {
  if (p.whereDash) return link(p.whereDash, p.where);
  return p.where || make("span", "dash-muted", NONE);
}

function grouped(found: Problem[], problem: string, what: string, where: string, whereDash: string, cause: string, ref: Problem["refs"][number]): void {
  var same = found.filter(function (p) {
    return p.problem === problem && p.what === what && p.where === where && p.cause === cause;
  })[0];
  if (same) same.refs.push(ref);
  else found.push({ problem: problem, what: what, where: where, whereDash: whereDash, cause: cause, refs: [ref] });
}

export interface Faults {
  unwired: MachineRef[];
  noGenerator: MachineRef[];
  starved: StarvedGenerator[];
}

export function faultsOf(data: CircuitsResponse): Faults {
  return { unwired: data.unwired, noGenerator: data.no_generator, starved: data.starved };
}

export function faultCount(f: Faults): number {
  return f.unwired.length + f.noGenerator.length + f.starved.length;
}

export function faultWords(f: Faults): string {
  return [
    count(f.unwired.length) + " " + W.noWire,
    count(f.noGenerator.length) + " " + W.noGenerator,
    counted(f.starved.length, W.starvedGenerator),
  ].join(" · ");
}

function problems(f: Faults): Problem[] {
  var found: Problem[] = [];
  f.starved.forEach(function (g) {
    grouped(found, W.starvedGenerator, g.name, "", "", g.cause, g);
  });
  f.unwired.forEach(function (m) {
    var at = whereOf(m);
    grouped(found, W.noWire, m.name, at.where, at.dash, "", m);
  });
  f.noGenerator.forEach(function (m) {
    var at = whereOf(m);
    grouped(found, W.noGenerator, m.name, at.where, at.dash, "", m);
  });
  return found;
}

export function problemTable(parent: HTMLElement, f: Faults, shown?: number): void {
  var rows = problems(f);
  var cut = shown === undefined ? rows : rows.slice(0, shown);
  parent.appendChild(
    table<Problem>(
      [
        {
          key: "problem",
          label: "problem",
          className: "bad dash-nowrap",
          render: function (p) {
            return p.problem;
          },
        },
        {
          key: "n",
          label: "machines",
          align: "right",
          render: function (p) {
            return count(p.refs.length);
          },
        },
        {
          key: "what",
          label: "machine",
          className: "dash-nowrap",
          render: function (p) {
            return p.what;
          },
        },
        {
          key: "where",
          label: "where",
          render: function (p) {
            return p.cause || whereCell(p);
          },
        },
        {
          key: "map",
          label: "",
          align: "right",
          render: function (p) {
            return pointButton(p.refs[0]!, "show " + p.what + " (" + p.problem + ") on the map");
          },
        },
      ],
      cut,
      {
        rowTitle: function (p) {
          return p.refs
            .map(function (r) {
              return r.instance;
            })
            .join(", ");
        },
        caption: W.powerProblems,
      }
    )
  );
  if (cut.length < rows.length) note(parent, "showing " + cut.length + " of " + rows.length + " rows; Power lists all");
}

function generatorLine(groups: GeneratorGroup[]): string {
  return groups
    .map(function (g) {
      return g.count + "× " + g.name + " " + mw(g.mw);
    })
    .join(" · ");
}

function unratedLine(classes: string[]): string {
  return "left out: " + counted(classes.length, "generator type") + " game data cannot rate";
}

function offGrid(data: CircuitsResponse): string {
  var off = data.off_grid;
  var parts = [counted(off.consumers, "machine") + (off.paused ? " (" + count(off.paused) + " " + W.paused + ")" : "") + " rated " + mw(off.draw_mw)];
  if (off.generators) parts.push(counted(off.generators, "generator") + " rated " + mw(off.generation_mw));
  return "on no wire, so in no figure above: " + parts.join(" · ");
}

function refTable(parent: HTMLElement, title: string, rows: MachineRef[]): void {
  if (!rows.length) return;
  var card = make("section", "dash-card");
  heading(card, title + " (" + rows.length + ")");
  var found: Problem[] = [];
  rows.forEach(function (m) {
    var at = whereOf(m);
    grouped(found, "", m.name, at.where, at.dash, "", m);
  });
  card.appendChild(
    table<Problem>(
      [
        {
          key: "n",
          label: "count",
          align: "right",
          render: function (p) {
            return count(p.refs.length);
          },
        },
        {
          key: "what",
          label: "generator",
          render: function (p) {
            return p.what;
          },
        },
        {
          key: "where",
          label: "where",
          render: function (p) {
            return whereCell(p);
          },
        },
        {
          key: "map",
          label: "",
          align: "right",
          render: function (p) {
            return pointButton(p.refs[0]!, "show " + p.what + " on the map");
          },
        },
      ],
      found,
      {
        rowTitle: function (p) {
          return p.refs
            .map(function (r) {
              return r.instance;
            })
            .join(", ");
        },
        caption: title,
      }
    )
  );
  parent.appendChild(card);
}

function problemCard(parent: HTMLElement, f: Faults): void {
  var n = faultCount(f);
  if (!n) return;
  var card = make("section", "dash-card");
  heading(card, W.powerProblems + " (" + count(n) + ")");
  problemTable(card, f);
  parent.appendChild(card);
}

function circuitsMissing(body: HTMLElement): void {
  var v = vitals();
  if (v.circuitsError) error(body, "power circuits", "", retryCircuits);
  else loading(body, "power circuits");
}

export function renderPower(body: HTMLElement): void {
  var data = vitals().circuits;
  if (!data) {
    circuitsMissing(body);
    return;
  }
  body.appendChild(ledgerTiles(world(data), starvedSub(data.world)));
  var whole = make("section", "dash-card");
  heading(whole, "whole world");
  whole.appendChild(bar(data.world, true));
  var facts: string[] = [];
  if (data.generators.length) facts.push(generatorLine(data.generators));
  if (data.paused) facts.push(counted(data.paused, "paused building") + ", left out of both sides");
  if (data.off_grid.consumers || data.off_grid.generators) facts.push(offGrid(data));
  if (data.unmodellable.length) facts.push(unratedLine(data.unmodellable));
  facts.forEach(function (f) {
    note(whole, f);
  });
  body.appendChild(whole);
  var card = make("section", "dash-card");
  heading(card, counted(data.circuits.length, "circuit"));
  circuitTable(card, data.circuits);
  note(card, "a circuit is what the wires join; switches read as closed, batteries are not counted");
  body.appendChild(card);
  problemCard(body, faultsOf(data));
  refTable(body, "generators on no wire", data.unwired_generators);
}

export function renderCircuit(body: HTMLElement, subject: string): void {
  body.appendChild(link("power", "‹ all circuits", "dash-back"));
  var data = vitals().circuits;
  if (!data) {
    circuitsMissing(body);
    return;
  }
  var row = data.circuits[+subject - 1];
  if (!row) {
    empty(body, "no circuit " + subject + " in this world", "circuit numbers can change between saves; pick one from the list");
    return;
  }
  var index = row.index;
  var head = make("div", "dash-title");
  head.appendChild(make("h1", "", circuitName(row)));
  if (row.bbox_m) {
    head.appendChild(
      mapButton(
        "fly the map to this circuit",
        function () {
          showCircuit(index);
        },
        "show circuit " + (index + 1) + " on the map"
      )
    );
  }
  body.appendChild(head);
  note(body, consumers(row) + " · " + counted(row.poles, "pole or tower", LEDGER.poles));
  var r = rated(row);
  var stranded = data.no_generator.filter(function (m) {
    return m.circuit === index;
  });
  body.appendChild(ledgerTiles(r, r.dark ? counted(stranded.length, "machine") + " wired here draw from nothing" : starvedSub(row.ledger)));
  body.appendChild(bar(row.ledger));
  if (row.generators.length) note(body, generatorLine(row.generators));
  if (row.unmodellable.length) note(body, unratedLine(row.unmodellable));
  problemCard(body, { unwired: [], noGenerator: stranded, starved: row.starved });
  note(body, "circuit numbers follow size and can change with the next save");
}
