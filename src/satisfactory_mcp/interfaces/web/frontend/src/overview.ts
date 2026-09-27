/* The dashboard's Overview tab: headline tiles, the factories and machines that need action,
 * and power per circuit, addressed as `dash=overview`. */

import { heading, link, note, table, tile } from "./dashkit";
import { count, make } from "./dom";
import { mw, pct, spoken } from "./format";
import { hashFor } from "./map";
import { vitals } from "./panel";
import { stateTone } from "./placements";
import { milestoneTile } from "./progress";
import { isFine, needsAction, stateSets } from "./states";
import { factoryMapButton, go, pointButton } from "./dashboard";
import { circuitTable, headroomTiles } from "./power-tab";
import { bar as powerBar } from "./powerview";

import type { FactoryHealthRow, MachineIssue, MachineRef, StarvedGenerator } from "./api-shapes";

var ATTENTION_SHOWN = 12;

interface Mix {
  bad: number;
  mid: number;
  ok: number;
}

export function actionable(): string[] {
  return stateSets().actionable;
}

function middling(): string[] {
  var h = vitals().health;
  return (h ? h.states : []).filter(function (s) {
    return !needsAction(s) && !isFine(s);
  });
}

export function mixOf(rows: FactoryHealthRow[]): Mix {
  var mix = { bad: 0, mid: 0, ok: 0 };
  rows.forEach(function (row) {
    row.states.forEach(function (s) {
      if (needsAction(s.state)) mix.bad += s.count;
      else if (isFine(s.state)) mix.ok += s.count;
      else mix.mid += s.count;
    });
  });
  return mix;
}

export function mixBar(mix: Mix): HTMLElement {
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

function powerProblems(): { total: number; sub: string } | null {
  var c = vitals().circuits;
  if (!c) return null;
  return {
    total: c.unwired.length + c.no_generator.length + c.starved.length,
    sub: c.unwired.length + " no wire · " + c.no_generator.length + " no generator · " + c.starved.length + " starved generators",
  };
}

export function renderOverview(body: HTMLElement): void {
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
  var milestones = milestoneTile();
  if (milestones) tiles.appendChild(milestones);
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
    left.appendChild(
      table<FactoryHealthRow>(
        [
          {
            key: "name",
            label: "factory",
            render: function (r) {
              return link("factories/" + r.name, r.name);
            },
          },
          {
            key: "actionable",
            label: "need action",
            align: "right",
            className: "bad",
            render: function (r) {
              return r.actionable;
            },
          },
          {
            key: "uptime",
            label: "uptime",
            align: "right",
            render: function (r) {
              return pct(r.uptime);
            },
          },
          { key: "map", label: "", align: "right", render: factoryMapButton },
        ],
        worst,
        {
          onRow: function (r) {
            go("factories/" + r.name);
          },
          caption: "factories needing action",
        }
      )
    );
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
  var total = 0;
  v.health.factories.forEach(function (r) {
    total += r.actionable;
    r.worst_actionable.forEach(function (issue) {
      found.push({ factory: r.name, issue: issue });
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
    li.appendChild(make("span", "dash-cause", g.cause));
    list.appendChild(li);
  });
  if (!list.childNodes.length) {
    note(parent, "no machine is " + spoken(actionable(), "or") + ", and none is dark");
    return;
  }
  parent.appendChild(list);
  var hidden = Math.max(0, total - Math.min(found.length, ATTENTION_SHOWN));
  note(parent, (hidden ? hidden + " more need action; " : "") + "the Factories tab has every factory");
}
