/* The dashboard: a whole-page view over the panel's two payloads and the milestone ladder,
 * addressed by the fragment's `dash=` key. See docs/frontend_vision.md §8. */

import { count, el, make } from "./dom";
import { mw, pct, phaseText, spoken } from "./format";
import { hashFor, writeHash } from "./map";
import { onVitals, showCircuit, showFactory, showPoint, vitals } from "./panel";
import { stateTone } from "./placements";
import { registerFetch } from "./registry";
import { onSetting, setSetting, setting, SETTINGS } from "./settings";
import { state } from "./state";

import type {
  CircuitRow,
  FactoryHealthRow,
  Ledger,
  MachineIssue,
  MachineRef,
  MilestoneRow,
  MilestonesResponse,
  StarvedGenerator,
} from "./api-shapes";

type Tab = "overview" | "factories" | "power" | "progress" | "settings";

var TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["factories", "Factories"],
  ["power", "Power"],
  ["progress", "Progress"],
  ["settings", "Settings"],
];

var FINE = ["saturated", "unmonitored"];

var ATTENTION_SHOWN = 12;

var progress = {
  data: null as MilestonesResponse | null,
  error: "",
};

var sort = { key: "actionable", desc: true };

var showDone = false;

function address(): { tab: Tab; subject: string } {
  var raw = state.dash;
  var cut = raw.indexOf("/");
  var head = cut < 0 ? raw : raw.slice(0, cut);
  var tab: Tab = "overview";
  TABS.forEach(function (t) {
    if (t[0] === head) tab = t[0];
  });
  return { tab: tab, subject: cut < 0 ? "" : raw.slice(cut + 1) };
}

function go(dash: string): void {
  location.hash = hashFor(dash);
}

function link(dash: string, text: string, className?: string): HTMLAnchorElement {
  var a = make("a", className, text);
  a.setAttribute("href", hashFor(dash));
  a.onclick = function (event) {
    event.stopPropagation();
    a.setAttribute("href", hashFor(dash));
  };
  return a;
}

function toMap(action: () => void): void {
  state.dash = "";
  history.pushState(null, "", hashFor(""));
  show();
  action();
  writeHash();
}

function mapButton(title: string, action: () => void): HTMLButtonElement {
  var button = make("button", "dash-map", "map");
  button.type = "button";
  button.title = title;
  button.onclick = function (event) {
    event.stopPropagation();
    toMap(action);
  };
  return button;
}

interface Placed {
  x_m: number;
  y_m: number;
}

function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
  return row.x_m !== null && row.y_m !== null;
}

function pointButton(row: { x_m: number | null; y_m: number | null }): HTMLElement {
  if (!located(row)) return make("span", "dash-muted", "–");
  var at = row;
  return mapButton("fly the map to it", function () {
    showPoint(at.x_m, at.y_m);
  });
}

function note(parent: HTMLElement, text: string): void {
  parent.appendChild(make("p", "dash-note", text));
}

function heading(parent: HTMLElement, text: string): void {
  parent.appendChild(make("h2", "dash-h", text));
}

function tile(label: string, value: string, sub: string, bad?: boolean, href?: string): HTMLElement {
  var box = make(href ? "a" : "div", "dash-tile" + (bad ? " bad" : ""));
  if (href) box.setAttribute("href", href);
  box.appendChild(make("span", "dash-tile-k", label));
  box.appendChild(make("span", "dash-tile-v", value));
  if (sub) box.appendChild(make("span", "dash-tile-sub", sub));
  return box;
}

interface Mix {
  bad: number;
  mid: number;
  ok: number;
}

function actionable(): string[] {
  var h = vitals().health;
  return h ? h.actionable_states : [];
}

function needsAction(name: string): boolean {
  return actionable().indexOf(name) >= 0;
}

function middling(): string[] {
  var h = vitals().health;
  return (h ? h.states : []).filter(function (s) {
    return !needsAction(s) && FINE.indexOf(s) < 0;
  });
}

function mixOf(rows: FactoryHealthRow[]): Mix {
  var mix = { bad: 0, mid: 0, ok: 0 };
  rows.forEach(function (row) {
    row.states.forEach(function (s) {
      if (needsAction(s.state)) mix.bad += s.count;
      else if (FINE.indexOf(s.state) >= 0) mix.ok += s.count;
      else mix.mid += s.count;
    });
  });
  return mix;
}

function mixBar(mix: Mix): HTMLElement {
  var total = mix.bad + mix.mid + mix.ok;
  var bar = make("div", "dash-mix");
  bar.setAttribute("role", "img");
  var parts: [keyof Mix, string][] = [
    ["bad", "need action"],
    ["mid", spoken(middling(), "or")],
    ["ok", "running or unmonitored"],
  ];
  var words: string[] = [];
  parts.forEach(function (p) {
    var n = mix[p[0]];
    words.push(n + " " + p[1]);
    if (!n || !total) return;
    var seg = make("span", "dash-mix-" + p[0]);
    seg.style.width = (n / total) * 100 + "%";
    bar.appendChild(seg);
  });
  bar.title = words.join(", ");
  bar.setAttribute("aria-label", bar.title);
  return bar;
}

function mixLegend(mix: Mix): HTMLElement {
  var legend = make("div", "dash-mix-legend");
  var parts: [keyof Mix, string][] = [
    ["bad", "need action"],
    ["mid", middling().join(", ")],
    ["ok", "running or unmonitored"],
  ];
  parts.forEach(function (p) {
    var item = make("span", "dash-key");
    item.appendChild(make("i", "dash-swatch dash-mix-" + p[0]));
    item.appendChild(document.createTextNode(count(mix[p[0]]) + " " + p[1]));
    legend.appendChild(item);
  });
  return legend;
}

function powerBar(ledger: Ledger): HTMLElement {
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

function headroomTiles(ledger: Ledger, href?: string, counts?: boolean): HTMLElement[] {
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

function table(headers: [string, string][], sortable: boolean, onSort?: () => void): HTMLTableElement {
  var t = make("table", "dash-table");
  var head = make("thead");
  var tr = make("tr");
  headers.forEach(function (h) {
    var th = make("th", h[1] === "name" ? "" : "num", h[0]);
    if (sortable && h[1]) {
      th.className += " sort" + (sort.key === h[1] ? (sort.desc ? " desc" : " asc") : "");
      th.setAttribute("aria-sort", sort.key === h[1] ? (sort.desc ? "descending" : "ascending") : "none");
      th.tabIndex = 0;
      var pick = function () {
        if (sort.key === h[1]) sort.desc = !sort.desc;
        else {
          sort.key = h[1];
          sort.desc = h[1] !== "name";
        }
        if (onSort) onSort();
      };
      th.onclick = pick;
      th.onkeydown = function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pick();
        }
      };
    }
    tr.appendChild(th);
  });
  head.appendChild(tr);
  t.appendChild(head);
  t.appendChild(make("tbody"));
  return t;
}

function cell(tr: HTMLElement, content: string | number | HTMLElement, className?: string): void {
  var td = make("td", className);
  if (content instanceof HTMLElement) td.appendChild(content);
  else td.textContent = String(content);
  tr.appendChild(td);
}

function powerProblems(): { total: number; sub: string } | null {
  var c = vitals().circuits;
  if (!c) return null;
  return {
    total: c.unwired.length + c.no_generator.length + c.starved.length,
    sub: c.unwired.length + " no wire · " + c.no_generator.length + " no generator · " + c.starved.length + " starved generators",
  };
}

function renderOverview(body: HTMLElement): void {
  var v = vitals();
  var tiles = make("div", "dash-tiles");
  var rows = v.health ? v.health.factories : [];
  if (v.health) {
    var todo = rows.filter(function (r) {
      return r.actionable > 0;
    }).length;
    var box = tile(
      "factories",
      count(rows.length) + " named",
      todo + " with machines that need action",
      false,
      hashFor("factories")
    );
    box.appendChild(mixBar(mixOf(rows)));
    box.appendChild(mixLegend(mixOf(rows)));
    tiles.appendChild(box);
  } else {
    tiles.appendChild(tile("factories", "–", v.healthError || "loading…"));
  }
  if (v.circuits) {
    var w = v.circuits.world;
    var gens = 0;
    v.circuits.generators.forEach(function (g) {
      gens += g.count;
    });
    var gen = tile("generation", mw(w.generation_mw), gens + " generators", false, hashFor("power"));
    gen.appendChild(powerBar(w));
    tiles.appendChild(gen);
    headroomTiles(w, hashFor("power")).forEach(function (t) {
      tiles.appendChild(t);
    });
  } else {
    tiles.appendChild(tile("power", "–", v.circuitsError || "loading…"));
  }
  if (v.health) {
    var action = 0;
    rows.forEach(function (r) {
      action += r.actionable;
    });
    tiles.appendChild(
      tile("machines needing action", count(action), spoken(actionable(), "or") + ", in named factories", action > 0, hashFor("factories"))
    );
  }
  var dark = powerProblems();
  if (dark) tiles.appendChild(tile("power problems", count(dark.total), dark.sub, dark.total > 0, hashFor("power")));
  if (progress.data) {
    var ready = shown(progress.data).milestones.filter(function (m) {
      return m.status === "READY";
    }).length;
    var top = progress.data.highest_complete_tier;
    tiles.appendChild(
      tile(
        "milestones",
        top === null ? "no tier complete" : "tier " + top + " complete",
        (phaseText(progress.data.game_phase) || "no phase in this save") + " · " + ready + " affordable",
        false,
        hashFor("progress")
      )
    );
  }
  body.appendChild(tiles);

  var split = make("div", "dash-split");
  var left = make("section", "dash-card");
  heading(left, "factories needing action");
  var worst = rows.filter(function (r) {
    return r.actionable > 0;
  });
  if (!v.health) note(left, v.healthError || "loading…");
  else if (!worst.length) note(left, "no named factory has a machine in a state that needs action");
  else {
    var t = table(
      [
        ["factory", "name"],
        ["need action", ""],
        ["uptime", ""],
        ["", ""],
      ],
      false
    );
    var tb = t.tBodies[0]!;
    worst.forEach(function (r) {
      var tr = make("tr", "go");
      cell(tr, link("factories/" + r.name, r.name));
      cell(tr, r.actionable, "num bad");
      cell(tr, pct(r.uptime), "num");
      cell(tr, factoryMapButton(r), "num");
      tr.onclick = function () {
        go("factories/" + r.name);
      };
      tb.appendChild(tr);
    });
    left.appendChild(t);
  }
  split.appendChild(left);

  var right = make("section", "dash-card");
  heading(right, "machines needing action");
  renderAttention(right);
  split.appendChild(right);
  body.appendChild(split);

  var circuits = make("section", "dash-card");
  heading(circuits, "power per circuit");
  if (v.circuits) circuitTable(circuits, v.circuits.circuits);
  else note(circuits, v.circuitsError || "loading…");
  body.appendChild(circuits);
}

interface Attention {
  factory: string;
  issue: MachineIssue;
}

function renderAttention(parent: HTMLElement): void {
  var v = vitals();
  if (!v.health || !v.circuits) {
    note(parent, v.healthError || v.circuitsError || "loading…");
    return;
  }
  var found: Attention[] = [];
  v.health.factories.forEach(function (r) {
    r.worst.forEach(function (issue) {
      if (needsAction(issue.state)) found.push({ factory: r.name, issue: issue });
    });
  });
  var list = make("ul", "dash-list");
  found.slice(0, ATTENTION_SHOWN).forEach(function (a) {
    var li = make("li", "dash-issue");
    li.appendChild(make("span", "dash-state " + stateTone(a.issue.state, true), a.issue.state));
    li.appendChild(make("span", "dash-what", a.issue.what));
    li.appendChild(link("factories/" + a.factory, a.factory, "dash-where"));
    li.appendChild(pointButton(a.issue));
    if (a.issue.cause.length) li.appendChild(make("span", "dash-cause", a.issue.cause.join(", ")));
    list.appendChild(li);
  });
  var dark: [string, MachineRef[]][] = [
    ["no wire", v.circuits.unwired],
    ["no generator", v.circuits.no_generator],
  ];
  dark.forEach(function (d) {
    d[1].slice(0, ATTENTION_SHOWN).forEach(function (m) {
      var li = make("li", "dash-issue");
      li.appendChild(make("span", "dash-state", d[0]));
      li.appendChild(make("span", "dash-what", m.name));
      li.appendChild(pointButton(m));
      li.title = m.instance;
      list.appendChild(li);
    });
  });
  v.circuits.starved.slice(0, ATTENTION_SHOWN).forEach(function (g: StarvedGenerator) {
    var li = make("li", "dash-issue");
    li.appendChild(make("span", "dash-state", "starved generator"));
    li.appendChild(make("span", "dash-what", g.name));
    li.appendChild(pointButton(g));
    li.appendChild(make("span", "dash-cause", "out of " + g.missing.join(", ")));
    list.appendChild(li);
  });
  if (!list.childNodes.length) {
    note(parent, "no machine is " + spoken(actionable(), "or") + ", and none is dark");
    return;
  }
  parent.appendChild(list);
  var hidden = Math.max(0, found.length - ATTENTION_SHOWN);
  note(
    parent,
    "each factory sends its " +
      "worst eight machines; " +
      (hidden ? hidden + " more of those are not shown here; " : "") +
      "the Factories tab has every factory"
  );
}

function factoryMapButton(row: FactoryHealthRow): HTMLElement {
  if (!row.bbox_m) return make("span", "dash-muted", "–");
  return mapButton("fly the map to this factory", function () {
    showFactory(row.name);
  });
}

function sortValue(row: FactoryHealthRow, key: string): number | string {
  if (key === "name") return row.name.toLowerCase();
  if (key === "uptime") return row.uptime === null ? -1 : row.uptime;
  var value = (row as unknown as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

function renderFactories(body: HTMLElement): void {
  var v = vitals();
  if (!v.health) {
    note(body, v.healthError || "loading…");
    return;
  }
  var rows = v.health.factories.slice();
  if (!rows.length) {
    note(body, "no factories named yet — name one with the name_factory tool");
    return;
  }
  rows.sort(function (a, b) {
    var x = sortValue(a, sort.key);
    var y = sortValue(b, sort.key);
    var order = x < y ? -1 : x > y ? 1 : 0;
    return sort.desc ? -order : order;
  });
  note(body, rows.length + " named factories. Click a heading to sort, a row for its detail.");
  var t = table(
    [
      ["factory", "name"],
      ["machines", "alive"],
      ["uptime", "uptime"],
      ["need action", "actionable"],
      ["not fine", "attention"],
      ["no wire", "unwired"],
      ["no generator", "no_generator"],
      ["MW measured", "measured_mw"],
      ["MW nameplate", "nameplate_mw"],
      ["state mix", ""],
      ["", ""],
    ],
    true,
    render
  );
  var tb = t.tBodies[0]!;
  rows.forEach(function (r) {
    var tr = make("tr", "go");
    cell(tr, link("factories/" + r.name, r.name));
    cell(tr, r.alive + (r.alive === r.anchors ? "" : " of " + r.anchors), "num");
    cell(tr, pct(r.uptime), "num");
    cell(tr, r.actionable, "num" + (r.actionable ? " bad" : ""));
    cell(tr, r.attention, "num");
    cell(tr, r.unwired, "num" + (r.unwired ? " bad" : ""));
    cell(tr, r.no_generator, "num" + (r.no_generator ? " bad" : ""));
    cell(tr, count(Math.round(r.measured_mw)), "num");
    cell(tr, count(Math.round(r.nameplate_mw)), "num");
    cell(tr, mixBar(mixOf([r])), "mix");
    cell(tr, factoryMapButton(r), "num");
    tr.onclick = function () {
      go("factories/" + r.name);
    };
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  body.appendChild(wrap);
  note(
    body,
    "uptime is each machine's last 300 s window, averaged; " +
      "a blocked machine produces nothing, so a backed-up factory reads near 0%; " +
      "need action is " +
      spoken(actionable(), "or") +
      "; " +
      "not fine is everything but running and unmonitored"
  );
}

function renderFactory(body: HTMLElement, name: string): void {
  var v = vitals();
  if (!v.health) {
    note(body, v.healthError || "loading…");
    return;
  }
  var row = v.health.factories.filter(function (r) {
    return r.name === name;
  })[0];
  body.appendChild(link("factories", "‹ all factories", "dash-back"));
  if (!row) {
    note(body, "no factory named “" + name + "” in this save");
    return;
  }
  var head = make("div", "dash-title");
  head.appendChild(make("h1", "", row.name));
  head.appendChild(factoryMapButton(row));
  body.appendChild(head);
  if (row.review) note(body, "label " + row.review + ": " + row.alive + " of " + row.anchors + " anchors still stand");
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("machines", count(row.machines), row.alive + " of " + row.anchors + " anchors standing"));
  tiles.appendChild(tile("uptime", pct(row.uptime), "mean over each machine's last 300 s"));
  tiles.appendChild(tile("need action", count(row.actionable), row.attention + " not fine in all", row.actionable > 0));
  tiles.appendChild(tile("draw, measured", mw(row.measured_mw), mw(row.nameplate_mw) + " nameplate"));
  tiles.appendChild(
    tile("power", count(row.unwired + row.no_generator), row.unwired + " no wire · " + row.no_generator + " no generator", row.unwired + row.no_generator > 0)
  );
  body.appendChild(tiles);

  var split = make("div", "dash-split");
  var states = make("section", "dash-card");
  heading(states, "machines by state");
  var biggest = 1;
  row.states.forEach(function (s) {
    biggest = Math.max(biggest, s.count);
  });
  var t = table(
    [
      ["state", "name"],
      ["machines", ""],
      ["", ""],
    ],
    false
  );
  var tb = t.tBodies[0]!;
  row.states.forEach(function (s) {
    var tr = make("tr");
    var bad = needsAction(s.state);
    var ok = FINE.indexOf(s.state) >= 0;
    var tone = stateTone(s.state, bad);
    cell(tr, s.state, tone);
    cell(tr, s.count, "num");
    var bar = make("div", "dash-hbar");
    var fill = make("span", "dash-mix-" + (tone || (ok ? "ok" : "mid")));
    fill.style.width = (s.count / biggest) * 100 + "%";
    bar.appendChild(fill);
    cell(tr, bar, "bar");
    tb.appendChild(tr);
  });
  states.appendChild(t);
  split.appendChild(states);

  var worst = make("section", "dash-card");
  heading(worst, "worst machines");
  if (!row.worst.length) note(worst, "every machine is running or unmonitored");
  else {
    var list = make("ul", "dash-list");
    row.worst.forEach(function (issue) {
      var li = make("li", "dash-issue");
      li.appendChild(make("span", "dash-state " + stateTone(issue.state, true), issue.state));
      li.appendChild(make("span", "dash-what", issue.what));
      li.appendChild(make("span", "dash-where", pct(issue.uptime) + " up"));
      li.appendChild(pointButton(issue));
      if (issue.cause.length) li.appendChild(make("span", "dash-cause", issue.cause.join(", ")));
      li.title = issue.instance;
      list.appendChild(li);
    });
    worst.appendChild(list);
    var rest = row.attention - row.worst.length;
    if (rest > 0) note(worst, rest + " more are not fine; factory_health lists them all");
  }
  split.appendChild(worst);
  body.appendChild(split);
}

function circuitTable(parent: HTMLElement, rows: CircuitRow[]): void {
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

function renderPower(body: HTMLElement): void {
  var v = vitals();
  var data = v.circuits;
  if (!data) {
    note(body, v.circuitsError || "loading…");
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

function renderCircuit(body: HTMLElement, subject: string): void {
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

function amounts(rows: { name: string; amount: number }[]): string {
  return (
    rows
      .map(function (r) {
        return count(r.amount) + " " + r.name;
      })
      .join(", ") || "–"
  );
}

function reach(data: MilestonesResponse): number {
  var top = 0;
  data.milestones.forEach(function (m) {
    if (m.status === "DONE") top = Math.max(top, m.tier);
  });
  if (!top && data.tiers.length) top = data.tiers[0]!.tier;
  return top;
}

function shown(data: MilestonesResponse): MilestonesResponse {
  if (setting("spoilers")) return data;
  var top = reach(data);
  return {
    game_phase: data.game_phase,
    highest_complete_tier: data.highest_complete_tier,
    tiers: data.tiers.filter(function (t) {
      return t.tier <= top;
    }),
    milestones: data.milestones.filter(function (m) {
      return m.tier <= top;
    }),
  };
}

function renderProgress(body: HTMLElement): void {
  if (!progress.data) {
    note(body, progress.error || "loading…");
    return;
  }
  var data = shown(progress.data);
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(
    tile(
      "highest complete tier",
      data.highest_complete_tier === null ? "none" : String(data.highest_complete_tier),
      phaseText(data.game_phase) || "no phase in this save"
    )
  );
  var counts: Record<string, number> = { READY: 0, short: 0, BLOCKED: 0, DONE: 0 };
  data.milestones.forEach(function (m) {
    counts[m.status] = (counts[m.status] || 0) + 1;
  });
  tiles.appendChild(tile("affordable now", count(counts.READY!), "bill covered by spendable stock"));
  tiles.appendChild(tile("short", count(counts.short!), "stock does not cover the bill"));
  tiles.appendChild(tile("done", count(counts.DONE!), "of " + data.milestones.length + " milestones" + (setting("spoilers") ? "" : " so far")));
  body.appendChild(tiles);

  var strip = make("div", "dash-tiers");
  data.tiers.forEach(function (t) {
    var box = make("div", "dash-tier" + (t.done === t.total ? " full" : ""));
    box.appendChild(make("span", "dash-tier-k", "tier " + t.tier));
    var bar = make("div", "dash-hbar");
    var fill = make("span", "dash-mix-ok");
    fill.style.width = (t.total ? (t.done / t.total) * 100 : 0) + "%";
    bar.appendChild(fill);
    box.appendChild(bar);
    box.appendChild(make("span", "dash-tier-v", t.done + "/" + t.total));
    strip.appendChild(box);
  });
  body.appendChild(strip);
  if (!setting("spoilers")) {
    var hid = make("p", "dash-note", "Tiers you have not started are hidden. ");
    hid.appendChild(link("settings", "Settings"));
    hid.appendChild(document.createTextNode(" can show them."));
    body.appendChild(hid);
  }

  var card = make("section", "dash-card");
  var bar2 = make("div", "dash-title");
  bar2.appendChild(make("h2", "dash-h", "milestones"));
  var toggle = make("label", "dash-toggle");
  var box2 = make("input");
  box2.type = "checkbox";
  box2.checked = showDone;
  box2.onchange = function () {
    showDone = box2.checked;
    render();
  };
  toggle.appendChild(box2);
  toggle.appendChild(document.createTextNode(" show done"));
  bar2.appendChild(toggle);
  card.appendChild(bar2);
  var rows = data.milestones.filter(function (m: MilestoneRow) {
    return showDone || m.status !== "DONE";
  });
  if (!rows.length) note(card, "every milestone is done");
  else {
    var t = table(
      [
        ["tier", ""],
        ["milestone", "name"],
        ["status", "name"],
        ["cost", "name"],
        ["short by", "name"],
        ["recipes", ""],
      ],
      false
    );
    var tb = t.tBodies[0]!;
    rows.forEach(function (m) {
      var tr = make("tr");
      cell(tr, m.tier, "num");
      cell(tr, m.name);
      cell(tr, m.status + (m.blocked_by.length ? " (" + m.blocked_by.join(", ") + ")" : ""), m.status === "READY" ? "ok" : "");
      cell(tr, amounts(m.cost));
      cell(tr, m.status === "DONE" ? "" : amounts(m.short), m.short.length && m.status !== "DONE" ? "bad" : "");
      cell(tr, m.unlocks || "", "num");
      tb.appendChild(tr);
    });
    var wrap = make("div", "dash-scroll");
    wrap.appendChild(t);
    card.appendChild(wrap);
  }
  note(
    card,
    "cost is checked against spendable stock: carried, storage containers and the Dimensional Depot. " +
      "READY is about the bill, not about access: a HUB tier opens with Space Elevator deliveries, " +
      "which no milestone in the game data records. recipes counts what a milestone newly grants."
  );
  body.appendChild(card);
}

function renderSettings(body: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "settings");
  note(card, "Kept in this browser only. Nothing is sent to the server.");
  SETTINGS.forEach(function (s) {
    var row = make("label", "dash-setting");
    var box = make("input");
    box.type = "checkbox";
    box.checked = setting(s.key);
    box.onchange = function () {
      setSetting(s.key, box.checked);
    };
    row.appendChild(box);
    var words = make("span", "dash-setting-text");
    words.appendChild(make("span", "dash-setting-k", s.label));
    words.appendChild(make("span", "dash-setting-hint", s.hint));
    row.appendChild(words);
    card.appendChild(row);
  });
  body.appendChild(card);
}

function renderNav(tab: Tab): void {
  var nav = el("dash-nav");
  nav.textContent = "";
  TABS.forEach(function (t) {
    var a = link(t[0], t[1], "dash-tab" + (t[0] === tab ? " on" : ""));
    if (t[0] === tab) a.setAttribute("aria-current", "page");
    nav.appendChild(a);
  });
}

function render(): void {
  if (!state.dash) return;
  var at = address();
  renderNav(at.tab);
  var body = el("dash-body");
  var scroll = el("dash").scrollTop;
  body.textContent = "";
  if (at.tab === "overview") renderOverview(body);
  else if (at.tab === "factories") {
    if (at.subject) renderFactory(body, at.subject);
    else renderFactories(body);
  } else if (at.tab === "power") {
    if (at.subject) renderCircuit(body, at.subject);
    else renderPower(body);
  } else if (at.tab === "progress") renderProgress(body);
  else renderSettings(body);
  el("dash").scrollTop = scroll;
}

function show(): void {
  var on = !!state.dash;
  document.body.classList.toggle("dash-on", on);
  el("dash").hidden = !on;
  var views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (a: HTMLAnchorElement) {
    var mine = (a.getAttribute("data-view") === "dash") === on;
    a.className = "view-link" + (mine ? " on" : "");
  });
  render();
}

export function applyDash(raw: string): void {
  if (raw === state.dash) return;
  var before = address().tab + "/" + address().subject;
  state.dash = raw;
  if (address().tab + "/" + address().subject !== before) el("dash").scrollTop = 0;
  show();
}

function wire(): void {
  var views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (a: HTMLAnchorElement) {
    var dash = a.getAttribute("data-view") === "dash";
    a.onclick = function (event) {
      if (dash) {
        a.setAttribute("href", hashFor(state.dash || "overview"));
        return;
      }
      event.preventDefault();
      if (state.dash) toMap(function () {});
    };
  });
  onVitals(render);
  onSetting(render);
}

wire();
show();

registerFetch<MilestonesResponse>({
  wave: "live",
  rank: 60,
  path: "/api/progress/milestones",
  label: "milestones",
  clears: [],
  refilters: false,
  draw: function (data) {
    progress.data = data;
    progress.error = "";
    render();
  },
  failed: function () {
    progress.data = null;
    progress.error = "milestones could not be read for this save";
    render();
  },
});
