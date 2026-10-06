/* The dashboard's Power tab: the world ledger, every circuit and the machines without power,
 * addressed as `dash=power[/<circuit>]`. */

import { appendNote, empty, error, heading, link, loading, table, tile } from "../kit/dashkit";
import { make } from "../kit/dom";
import { count, mw } from "../kit/format";
import { loadOne } from "../app/load";
import { showCircuit } from "../map/panel";
import { vitals } from "../app/vitals";
import { go } from "../app/nav";
import { counted, WORDS } from "../kit/words";
import { mapButton, pointButton } from "./actions";
import {
  biomassLine,
  circuitDark,
  circuitName,
  ledgerBar,
  NONE,
  ratedCircuit,
  ratedWorld,
  readGeneration,
  readHeadroomFull,
  readHeadroomNow,
  readMeasuredDraw,
  unrated,
  unratedTitle,
  whereOf,
} from "./power-ledger";

import type { Column } from "../kit/dashkit";
import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, StarvedGenerator } from "../api/shapes";
import type { Rated, Where } from "./power-ledger";

type GeneratorGroup = CircuitsResponse["generators"][number];

type MachinePlace = { instance: string; x_m: number | null; y_m: number | null };

/* Machines of one kind with one fault at one place, listed as one row. */
interface MachineGroup {
  label: string;
  what: string;
  where: Where;
  cause: string;
  refs: MachinePlace[];
}

interface MachineEntry {
  label: string;
  what: string;
  where: Where;
  cause: string;
  ref: MachinePlace;
}

const NOWHERE: Where = { where: "", dash: "" };

export function retryCircuits(): void {
  loadOne("/api/power/circuits");
}

/* `withCounts` adds how many machines the measured figure stands on. */
export function headroomTiles(rated: Rated, options?: { href?: string; withCounts?: boolean }): HTMLElement[] {
  const opts = options || {};
  const ledger = rated.ledger;
  const nowReading = readHeadroomNow(rated);
  const fullReading = readHeadroomFull(rated);
  const now = tile(
    WORDS.headroomNow,
    nowReading.value,
    nowReading.why || mw(ledger.measured_draw_mw) + " " + WORDS.measuredDraw + (opts.withCounts ? " · " + counted(ledger.monitored, "machine") + " measured" : ""),
    nowReading.bad,
    opts.href
  );
  const full = tile(
    WORDS.headroomFull,
    fullReading.value,
    fullReading.why ||
      mw(ledger.draw_mw) + " " + WORDS.nameplateDraw + (opts.withCounts && ledger.unmonitored ? " · " + count(ledger.unmonitored) + " unmeasured, charged in full" : ""),
    fullReading.bad,
    opts.href
  );
  if (unrated(rated)) now.title = full.title = unratedTitle(rated);
  return [now, full];
}

export function generationTile(rated: Rated, sub: string, starved: boolean, href?: string): HTMLElement {
  const reading = readGeneration(rated);
  const extra = reading.why ? "" : biomassLine(rated.ledger);
  const box = tile(WORDS.generation, reading.value, reading.why || (extra ? sub + " · " + extra : sub), reading.bad || (!reading.why && starved), href);
  if (unrated(rated)) box.title = unratedTitle(rated);
  else if (extra) box.title = extra + ": " + counted(rated.ledger.biomass_generators, "burner") + ", hand-fed; Settings can count them";
  return box;
}

function starvedSub(ledger: Ledger): string {
  return ledger.starved_generation_mw ? mw(ledger.starved_generation_mw) + " of it starved" : "none of it starved";
}

function ledgerTiles(rated: Rated, ledger: Ledger, sub: string): HTMLElement {
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(generationTile(rated, sub, ledger.starved_generation_mw > 0));
  headroomTiles(rated, { withCounts: true }).forEach(function (headroomTile) {
    tiles.appendChild(headroomTile);
  });
  return tiles;
}

function consumerText(row: CircuitRow): string {
  return counted(row.consumers, "consumer") + (row.ledger.paused ? ", " + count(row.ledger.paused) + " " + WORDS.paused : "");
}

function badWhen(bad: (row: CircuitRow) => boolean): (row: CircuitRow) => string {
  return function (row) {
    return bad(row) ? "bad" : "";
  };
}

function circuitMapButton(index: number): HTMLElement {
  return mapButton(
    "fly the map to this circuit",
    function () {
      showCircuit(index);
    },
    "show circuit " + (index + 1) + " on the map"
  );
}

function circuitColumns(): Column<CircuitRow>[] {
  return [
    {
      key: "circuit",
      label: "circuit",
      render: function (row) {
        const anchor = link("power/" + (row.index + 1), circuitName(row), "dash-trunc");
        anchor.title = circuitName(row);
        return anchor;
      },
    },
    {
      key: "generation",
      label: WORDS.generation,
      align: "right",
      tone: badWhen(circuitDark),
      render: function (row) {
        return readGeneration(ratedCircuit(row)).value;
      },
    },
    {
      key: "draw",
      label: WORDS.measuredDraw,
      align: "right",
      render: function (row) {
        return readMeasuredDraw(ratedCircuit(row)).value;
      },
    },
    {
      key: "now",
      label: WORDS.headroomNow,
      align: "right",
      tone: badWhen(function (row) {
        return readHeadroomNow(ratedCircuit(row)).bad;
      }),
      render: function (row) {
        return readHeadroomNow(ratedCircuit(row)).value;
      },
    },
    {
      key: "full",
      label: WORDS.headroomFull,
      align: "right",
      tone: badWhen(function (row) {
        return readHeadroomFull(ratedCircuit(row)).bad;
      }),
      render: function (row) {
        return readHeadroomFull(ratedCircuit(row)).value;
      },
    },
    {
      key: "consumers",
      label: "consumers",
      align: "right",
      title: "every machine on the circuit, paused ones too",
      render: function (row) {
        return count(row.consumers);
      },
    },
    {
      key: "bar",
      label: "",
      className: "bar",
      render: function (row) {
        return ledgerBar(row.ledger);
      },
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (row) {
        return row.bbox_m ? circuitMapButton(row.index) : make("span", "dash-muted", NONE);
      },
    },
  ];
}

function circuitRowTitle(row: CircuitRow): string {
  const rated = ratedCircuit(row);
  if (unrated(rated)) return unratedTitle(rated);
  const why = readMeasuredDraw(rated).why;
  return [why ? why + " on this circuit" : "", row.ledger.paused ? consumerText(row) : "", biomassLine(row.ledger)].filter(Boolean).join(" · ");
}

export function circuitTable(parent: HTMLElement, rows: CircuitRow[]): void {
  parent.appendChild(
    table<CircuitRow>(circuitColumns(), rows, {
      onRow: function (row) {
        go("power/" + (row.index + 1));
      },
      rowTitle: circuitRowTitle,
      caption: "power per circuit",
    })
  );
}

function whereCell(where: Where): string | HTMLElement {
  if (where.dash) return link(where.dash, where.where);
  return where.where || make("span", "dash-muted", NONE);
}

function groupMachines(entries: MachineEntry[]): MachineGroup[] {
  const groups: MachineGroup[] = [];
  entries.forEach(function (entry) {
    const same = groups.filter(function (group) {
      return group.label === entry.label && group.what === entry.what && group.where.where === entry.where.where && group.cause === entry.cause;
    })[0];
    if (same) same.refs.push(entry.ref);
    else groups.push({ label: entry.label, what: entry.what, where: entry.where, cause: entry.cause, refs: [entry.ref] });
  });
  return groups;
}

function instancesTitle(group: MachineGroup): string {
  return group.refs
    .map(function (ref) {
      return ref.instance;
    })
    .join(", ");
}

function machineGroupColumns(
  countLabel: string,
  whatLabel: string,
  mapTitle: (group: MachineGroup) => string,
  whatClassName?: string
): Column<MachineGroup>[] {
  return [
    {
      key: "n",
      label: countLabel,
      align: "right",
      render: function (group) {
        return count(group.refs.length);
      },
    },
    {
      key: "what",
      label: whatLabel,
      className: whatClassName,
      render: function (group) {
        return group.what;
      },
    },
    {
      key: "where",
      label: "where",
      render: function (group) {
        return group.cause || whereCell(group.where);
      },
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (group) {
        return pointButton(group.refs[0]!, mapTitle(group));
      },
    },
  ];
}

export interface Faults {
  unwired: MachineRef[];
  noGenerator: MachineRef[];
  starved: StarvedGenerator[];
}

export function faultsOf(data: CircuitsResponse): Faults {
  return { unwired: data.unwired, noGenerator: data.no_generator, starved: data.starved };
}

export function faultCount(faults: Faults): number {
  return faults.unwired.length + faults.noGenerator.length + faults.starved.length;
}

export function faultWords(faults: Faults): string {
  return [
    count(faults.unwired.length) + " " + WORDS.noWire,
    count(faults.noGenerator.length) + " " + WORDS.noGenerator,
    counted(faults.starved.length, WORDS.starvedGenerator),
  ].join(" · ");
}

function machineEntry(label: string, machine: MachineRef): MachineEntry {
  return { label: label, what: machine.name, where: whereOf(machine), cause: "", ref: machine };
}

function faultGroups(faults: Faults): MachineGroup[] {
  const entries: MachineEntry[] = [];
  faults.starved.forEach(function (generator) {
    entries.push({ label: WORDS.starvedGenerator, what: generator.name, where: NOWHERE, cause: generator.cause, ref: generator });
  });
  faults.unwired.forEach(function (machine) {
    entries.push(machineEntry(WORDS.noWire, machine));
  });
  faults.noGenerator.forEach(function (machine) {
    entries.push(machineEntry(WORDS.noGenerator, machine));
  });
  return groupMachines(entries);
}

export function problemTable(parent: HTMLElement, faults: Faults, shown?: number): void {
  const rows = faultGroups(faults);
  const cut = shown === undefined ? rows : rows.slice(0, shown);
  const problemColumn: Column<MachineGroup> = {
    key: "problem",
    label: "problem",
    className: "bad dash-nowrap",
    render: function (group) {
      return group.label;
    },
  };
  const columns = [problemColumn].concat(
    machineGroupColumns(
      "machines",
      "machine",
      function (group) {
        return "show " + group.what + " (" + group.label + ") on the map";
      },
      "dash-nowrap"
    )
  );
  parent.appendChild(table<MachineGroup>(columns, cut, { rowTitle: instancesTitle, caption: WORDS.powerProblems }));
  if (cut.length < rows.length) appendNote(parent, "showing " + cut.length + " of " + rows.length + " rows; Power lists all");
}

function generatorLine(groups: GeneratorGroup[]): string {
  return groups
    .map(function (group) {
      return group.count + "× " + group.name + " " + mw(group.mw);
    })
    .join(" · ");
}

function unratedLine(classes: string[]): string {
  return "left out: " + counted(classes.length, "generator type") + " game data cannot rate";
}

function offGrid(data: CircuitsResponse): string {
  const off = data.off_grid;
  const parts = [counted(off.consumers, "machine") + (off.paused ? " (" + count(off.paused) + " " + WORDS.paused + ")" : "") + " rated " + mw(off.draw_mw)];
  if (off.generators) parts.push(counted(off.generators, "generator") + " rated " + mw(off.generation_mw));
  return "on no wire, so in no figure above: " + parts.join(" · ");
}

function unwiredGeneratorTable(parent: HTMLElement, title: string, rows: MachineRef[]): void {
  if (!rows.length) return;
  const card = make("section", "dash-card");
  heading(card, title + " (" + rows.length + ")");
  const groups = groupMachines(
    rows.map(function (machine) {
      return machineEntry("", machine);
    })
  );
  const columns = machineGroupColumns("count", "generator", function (group) {
    return "show " + group.what + " on the map";
  });
  card.appendChild(table<MachineGroup>(columns, groups, { rowTitle: instancesTitle, caption: title }));
  parent.appendChild(card);
}

function problemCard(parent: HTMLElement, faults: Faults): void {
  const total = faultCount(faults);
  if (!total) return;
  const card = make("section", "dash-card");
  heading(card, WORDS.powerProblems + " (" + count(total) + ")");
  problemTable(card, faults);
  parent.appendChild(card);
}

function circuitsMissing(body: HTMLElement): void {
  const v = vitals();
  if (v.circuitsError) error(body, "power circuits", "", retryCircuits);
  else loading(body, "power circuits");
}

export function renderPower(body: HTMLElement): void {
  const data = vitals().circuits;
  if (!data) {
    circuitsMissing(body);
    return;
  }
  body.appendChild(ledgerTiles(ratedWorld(data), data.world, starvedSub(data.world)));
  const whole = make("section", "dash-card");
  heading(whole, "whole world");
  whole.appendChild(ledgerBar(data.world, true));
  const facts: string[] = [];
  if (data.generators.length) facts.push(generatorLine(data.generators));
  if (data.paused) facts.push(counted(data.paused, "paused building") + ", left out of both sides");
  if (data.off_grid.consumers || data.off_grid.generators) facts.push(offGrid(data));
  if (data.unmodellable.length) facts.push(unratedLine(data.unmodellable));
  facts.forEach(function (fact) {
    appendNote(whole, fact);
  });
  body.appendChild(whole);
  const card = make("section", "dash-card");
  heading(card, counted(data.circuits.length, "circuit"));
  circuitTable(card, data.circuits);
  appendNote(card, "a circuit is what the wires join; switches read as closed, batteries are not counted");
  body.appendChild(card);
  problemCard(body, faultsOf(data));
  unwiredGeneratorTable(body, "generators on no wire", data.unwired_generators);
}

export function renderCircuit(body: HTMLElement, subject: string): void {
  body.appendChild(link("power", "‹ all circuits", "dash-back"));
  const data = vitals().circuits;
  if (!data) {
    circuitsMissing(body);
    return;
  }
  const row = data.circuits[+subject - 1];
  if (!row) {
    empty(body, "no circuit " + subject + " in this world", "circuit numbers can change between saves; pick one from the list");
    return;
  }
  const index = row.index;
  const head = make("div", "dash-title");
  head.appendChild(make("h1", "", circuitName(row)));
  if (row.bbox_m) head.appendChild(circuitMapButton(index));
  body.appendChild(head);
  appendNote(body, consumerText(row) + " · " + counted(row.poles, "pole or tower", WORDS.polesAndTowers));
  const rated = ratedCircuit(row);
  const stranded = data.no_generator.filter(function (machine) {
    return machine.circuit === index;
  });
  body.appendChild(ledgerTiles(rated, row.ledger, rated.dark ? counted(stranded.length, "machine") + " wired here draw from nothing" : starvedSub(row.ledger)));
  body.appendChild(ledgerBar(row.ledger));
  if (row.generators.length) appendNote(body, generatorLine(row.generators));
  if (row.unmodellable.length) appendNote(body, unratedLine(row.unmodellable));
  problemCard(body, { unwired: [], noGenerator: stranded, starved: row.starved });
  appendNote(body, "circuit numbers follow size and can change with the next save");
}
