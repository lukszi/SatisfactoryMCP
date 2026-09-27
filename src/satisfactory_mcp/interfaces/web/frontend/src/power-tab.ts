/* The dashboard's Power tab: the world ledger, every circuit and the machines without power,
 * addressed as `dash=power[/<circuit>]`. */

import { error, heading, link, loading, note, table, tile } from "./dashkit";
import { make } from "./dom";
import { mw } from "./format";
import { loadOne } from "./load";
import { showCircuit, vitals } from "./panel";
import { go, mapButton, pointButton } from "./dashboard";
import { bar, circuitDark, circuitName, headroom } from "./powerview";

import type { CircuitRow, Ledger, MachineRef, StarvedGenerator } from "./api-shapes";

export function headroomTiles(ledger: Ledger, href?: string, counts?: boolean): HTMLElement[] {
  return [
    tile(
      "headroom now",
      headroom(ledger.measured_headroom_mw),
      mw(ledger.measured_draw_mw) + " measured draw" + (counts ? " · " + ledger.monitored + " machines measured" : ""),
      ledger.measured_headroom_mw < 0,
      href
    ),
    tile(
      "headroom at full rate",
      headroom(ledger.headroom_mw),
      mw(ledger.draw_mw) + " nameplate draw" + (counts ? " · " + ledger.unmonitored + " unmeasured, charged in full" : ""),
      ledger.headroom_mw < 0,
      href
    ),
  ];
}

export function circuitTable(parent: HTMLElement, rows: CircuitRow[]): void {
  function short(value: (r: CircuitRow) => boolean): (r: CircuitRow) => string {
    return function (r) {
      return value(r) ? "bad" : "";
    };
  }
  parent.appendChild(
    table<CircuitRow>(
      [
        {
          key: "circuit",
          label: "circuit",
          render: function (r) {
            return link("power/" + (r.index + 1), circuitName(r));
          },
        },
        {
          key: "generation",
          label: "generation",
          align: "right",
          tone: short(circuitDark),
          render: function (r) {
            return circuitDark(r) ? "no generator" : mw(r.ledger.generation_mw);
          },
        },
        {
          key: "draw",
          label: "draw, measured",
          align: "right",
          render: function (r) {
            return mw(r.ledger.measured_draw_mw);
          },
        },
        {
          key: "now",
          label: "headroom now",
          align: "right",
          tone: short(function (r) {
            return r.ledger.measured_headroom_mw < 0;
          }),
          render: function (r) {
            return headroom(r.ledger.measured_headroom_mw);
          },
        },
        {
          key: "full",
          label: "headroom at full rate",
          align: "right",
          tone: short(function (r) {
            return r.ledger.headroom_mw < 0;
          }),
          render: function (r) {
            return headroom(r.ledger.headroom_mw);
          },
        },
        {
          key: "consumers",
          label: "consumers",
          align: "right",
          render: function (r) {
            return r.consumers;
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
              ? mapButton("fly the map to this circuit", function () {
                  showCircuit(r.index);
                })
              : make("span", "dash-muted", "–");
          },
        },
      ],
      rows,
      {
        onRow: function (r) {
          go("power/" + (r.index + 1));
        },
        caption: "power per circuit",
      }
    )
  );
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
    li.appendChild(make("span", "dash-where", mw(g.mw) + " · " + g.cause));
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
  body.appendChild(bar(data.world, true));
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
  refList(split, "generators on no wire", data.unwired_generators, "capacity no circuit can draw on");
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
  head.appendChild(make("h1", "", circuitName(row)));
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
  body.appendChild(bar(row.ledger));
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
  var stranded = v.circuits.no_generator.filter(function (m) {
    return m.circuit === row!.index;
  });
  if (stranded.length) refList(body, "no generator on this circuit", stranded, "wired, but no generator stands on it");
  note(body, "circuit numbers follow size in this save and can change when the next save is read");
}
