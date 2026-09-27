/* The dashboard's Factories tab: the named factories, unnamed-cluster detection and the
 * production graph, addressed as `dash=factories[/<name>]`. */

import { get, latest, send } from "./api";
import { button, cell, heading, link, note, table as kitTable, tile } from "./dashkit";
import { count, make } from "./dom";
import { mw, pct, spoken } from "./format";
import { drawGraph } from "./graph";
import { hashFor } from "./map";
import { showBox, vitals } from "./panel";
import { stateTone } from "./placements";
import { refreshLabels, renamedTo } from "./rename";
import { amount, choice, onSetting, setting } from "./settings";
import { state } from "./state";
import { tone, toneClass } from "./states";
import { fail, friendly, note as said } from "./toast";
import { startTrace } from "./trace";
import { actionButton, factoryMapButton, go, mapButton, pointButton, renameButton, render, sort, table, toMap } from "./dashboard";
import { actionable, mixBar, mixOf } from "./overview";

import type { Ticket } from "./api";
import type {
  CandidateRow,
  CandidatesResponse,
  FactoryGraphResponse,
  FactoryHealthRow,
  Flow,
  ForgotResponse,
  GraphNode,
  NamedResponse,
} from "./api-shapes";

var detect = {
  busy: false,
  error: "",
  data: null as CandidatesResponse | null,
  world: "",
  edits: {} as Record<number, string>,
  skipped: {} as Record<number, boolean>,
  showSkipped: false,
  saving: -1,
  named: [] as NamedResponse[],
  showAll: false,
  asked: "",
};

var graphView = {
  source: "",
  subject: "",
  busy: false,
  error: "",
  data: null as FactoryGraphResponse | null,
  drawn: null as SVGSVGElement | null,
};

function sortValue(row: FactoryHealthRow, key: string): number | string {
  if (key === "name") return row.name.toLowerCase();
  if (key === "uptime") return row.uptime === null ? -1 : row.uptime;
  var value = (row as unknown as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

export function renderFactories(body: HTMLElement): void {
  var v = vitals();
  if (!v.health) {
    note(body, v.healthError || "loading…");
    return;
  }
  renderDetect(body);
  var rows = v.health.factories.slice();
  if (!rows.length) {
    note(body, "no factories named yet — detect them above, or name one with the name_factory tool");
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
    var named = make("span", "dash-named");
    named.appendChild(link("factories/" + r.name, r.name));
    named.appendChild(
      renameButton(r.name, named, function () {
        render();
      })
    );
    cell(tr, named);
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

function detectPath(): `/api/factories/candidates?${string}` {
  var fedOnly = detect.showAll ? false : setting("fedOnly");
  var least = detect.showAll ? 1 : Math.max(1, amount("minMachines"));
  return (
    "/api/factories/candidates?style=" +
    encodeURIComponent(choice("naming")) +
    "&fed_only=" +
    fedOnly +
    "&min_machines=" +
    least
  ) as `/api/factories/candidates?${string}`;
}

var detectRun: Ticket | null = null;

function runDetect(keep?: boolean): void {
  var ticket = latest("detect");
  detectRun = ticket;
  detect.busy = true;
  detect.error = "";
  detect.asked = detectAsked();
  render();
  get<CandidatesResponse>(detectPath())
    .then(function (data) {
      if (!ticket.fresh()) return;
      detect.data = data;
      detect.world = state.world;
      if (!keep) {
        detect.edits = {};
        detect.skipped = {};
      }
    })
    .catch(function (error) {
      if (!ticket.fresh()) return;
      detect.data = null;
      detect.error = friendly(error);
    })
    .then(function () {
      if (detectRun !== ticket) return;
      detect.busy = false;
      render();
    });
}

function stale(error: unknown): boolean {
  return /changed elsewhere/.test(String(error));
}

function chosenName(row: CandidateRow): string {
  var edited = detect.edits[row.index];
  return (edited === undefined ? row.suggested_name : edited).trim();
}

function nameCandidate(row: CandidateRow): void {
  var data = detect.data;
  var name = chosenName(row);
  if (!data || !name || detect.saving >= 0) return;
  detect.saving = row.index;
  render();
  send<NamedResponse>("POST", "/api/labels", {
    name: name,
    proposal: row.index,
    as_of: data.token,
    version: data.version,
  })
    .then(function (reply) {
      if (detect.data) {
        detect.data.version = reply.version;
        detect.data.candidates = detect.data.candidates.filter(function (c) {
          return c.index !== row.index;
        });
      }
      detect.named.unshift(reply);
      said("named “" + reply.name + "”, " + reply.machines + " machines");
      reply.overlaps.forEach(fail);
      refreshLabels();
    })
    .catch(function (error) {
      fail("naming “" + name + "”: " + friendly(error));
      if (stale(error)) {
        refreshLabels();
        runDetect(true);
      }
    })
    .then(function () {
      detect.saving = -1;
      render();
    });
}

function forgetNamed(reply: NamedResponse): void {
  var version = detect.data ? detect.data.version : reply.version;
  send<ForgotResponse>("DELETE", "/api/labels/{name}", undefined, reply.name, "version=" + version)
    .then(function () {
      detect.named = detect.named.filter(function (n) {
        return n !== reply;
      });
      said("forgot “" + reply.name + "”");
    })
    .catch(function (error) {
      fail("forgetting “" + reply.name + "”: " + friendly(error));
    })
    .then(function () {
      refreshLabels();
      runDetect(true);
    });
}

function rates(flows: Flow[]): string {
  return flows
    .map(function (f) {
      return count(Math.round(f.per_min * 10) / 10) + " " + f.name;
    })
    .join(" · ");
}

function makes(row: CandidateRow): HTMLElement {
  var box = make("div", "dash-makes");
  var head = row.products.length
    ? rates(row.products) + " /min"
    : row.buildings
        .map(function (b) {
          return b.count + "× " + b.name;
        })
        .join(" · ") || "–";
  box.appendChild(make("span", "", head));
  if (row.intermediates.length) {
    box.appendChild(make("span", "dash-sub", "via " + rates(row.intermediates)));
  }
  if (row.sunk.length) box.appendChild(make("span", "dash-sub dash-sunk", "sunk " + rates(row.sunk)));
  if (row.unrouted.length) box.appendChild(make("span", "dash-sub", "goes nowhere " + rates(row.unrouted)));
  if (row.inputs.length) box.appendChild(make("span", "dash-sub", "in " + rates(row.inputs)));
  box.title = row.buildings
    .map(function (b) {
      return b.count + "× " + b.name;
    })
    .join(", ");
  return box;
}

function nameInput(row: CandidateRow): HTMLInputElement {
  var input = make("input", "dash-name" + (row.confident ? "" : " guess"));
  input.type = "text";
  var edited = detect.edits[row.index];
  input.value = edited === undefined ? row.suggested_name : edited;
  input.setAttribute("data-candidate", String(row.index));
  input.setAttribute("aria-label", "name for " + row.selector);
  if (!row.confident) input.title = "no clear end product, so this name is a guess";
  input.spellcheck = false;
  input.oninput = function () {
    detect.edits[row.index] = input.value;
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") nameCandidate(row);
    if (event.key === "Escape") {
      delete detect.edits[row.index];
      input.value = row.suggested_name;
    }
  };
  return input;
}

function candidateRow(tb: HTMLElement, row: CandidateRow): void {
  var tr = make("tr");
  var skipped = !!detect.skipped[row.index];
  if (skipped) tr.className = "dash-skipped";
  cell(tr, row.machines, "num");
  cell(tr, makes(row));
  cell(tr, row.fed, row.fed === "not fed" ? "dash-muted" : "");
  cell(tr, row.region || "–", row.region ? "" : "dash-muted");
  cell(tr, nameInput(row), "name");
  var acts = make("span", "dash-acts");
  var box = row.bbox_m;
  acts.appendChild(
    box
      ? mapButton("fly the map to this cluster and outline it", function () {
          showBox(box!);
        })
      : make("span", "dash-muted", "–")
  );
  acts.appendChild(
    actionButton("graph", "draw this cluster's production graph", function () {
      var data = detect.data;
      if (data) openGraph("candidate", row.selector, data.token);
    })
  );
  acts.appendChild(
    actionButton(
      detect.saving === row.index ? "naming…" : "name",
      "write this name to the label file",
      function () {
        nameCandidate(row);
      },
      detect.saving >= 0
    )
  );
  acts.appendChild(
    actionButton(skipped ? "unskip" : "skip", skipped ? "offer it again" : "leave it unnamed for now", function () {
      if (skipped) delete detect.skipped[row.index];
      else detect.skipped[row.index] = true;
      render();
    })
  );
  cell(tr, acts, "num");
  tr.title = row.selector + " · spread " + row.spread_m + " m";
  tb.appendChild(tr);
}

function namedList(card: HTMLElement): void {
  if (!detect.named.length) return;
  var done = make("ul", "dash-list");
  detect.named.forEach(function (reply) {
    var li = make("li", "dash-issue");
    li.appendChild(make("span", "dash-what", "named “" + reply.name + "”, " + reply.machines + " machines"));
    li.appendChild(
      actionButton("undo", "forget this label again", function () {
        forgetNamed(reply);
      })
    );
    li.appendChild(make("span", "dash-cause", "written to " + reply.stored_in));
    done.appendChild(li);
  });
  card.appendChild(done);
}

function skippedToggle(card: HTMLElement, hidden: number): void {
  if (!hidden) return;
  var toggle = make("label", "dash-toggle");
  var box = make("input");
  box.type = "checkbox";
  box.checked = detect.showSkipped;
  box.onchange = function () {
    detect.showSkipped = box.checked;
    render();
  };
  toggle.appendChild(box);
  toggle.appendChild(document.createTextNode(" show " + hidden + " skipped"));
  card.appendChild(toggle);
}

function hiddenNote(card: HTMLElement, data: CandidatesResponse): void {
  var parts: string[] = [];
  if (data.hidden.not_fed) parts.push(data.hidden.not_fed + " not fed");
  if (data.hidden.small) parts.push(data.hidden.small + " below " + data.min_machines + " machines");
  var line = make("p", "dash-note");
  if (detect.showAll) {
    line.appendChild(document.createTextNode("Showing every cluster. "));
    line.appendChild(
      actionButton("apply my filters", "hide what Settings says to hide", function () {
        detect.showAll = false;
        runDetect(true);
      })
    );
  } else if (parts.length) {
    var total = data.hidden.not_fed + data.hidden.small;
    line.appendChild(document.createTextNode(total + " hidden: " + parts.join(", ") + ". "));
    line.appendChild(
      actionButton("show all", "list every unnamed cluster, filters off", function () {
        detect.showAll = true;
        runDetect(true);
      })
    );
    line.appendChild(document.createTextNode(" "));
    line.appendChild(link("settings", "filters"));
  } else return;
  card.appendChild(line);
}

function renderDetect(body: HTMLElement): void {
  if (detect.data && detect.world !== state.world) {
    detect.data = null;
    detect.named = [];
  }
  var data = detect.data;
  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", "unnamed factories"));
  var label = detect.busy ? "detecting…" : data ? "detect again" : "Detect factories";
  bar.appendChild(
    actionButton(label, "find the machine clusters no label covers", function () {
      runDetect();
    }, detect.busy)
  );
  card.appendChild(bar);
  if (detect.error) note(card, "detection failed: " + detect.error);
  namedList(card);
  if (graphView.source === "candidate") graphCard(card);
  if (!data) {
    note(card, "Finds the machine clusters no factory label covers yet, and suggests a name for each from what it makes.");
    body.appendChild(card);
    return;
  }
  var open = data.candidates.filter(function (c) {
    return !detect.skipped[c.index];
  });
  if (!data.candidates.length) note(card, "no unnamed cluster left to show");
  else {
    note(
      card,
      open.length +
        (open.length === 1 ? " unnamed cluster" : " unnamed clusters") +
        ", largest first. Makes: products (what reaches a box or leaves the cluster) per minute at nameplate, then what they are made via; sunk is what goes to the AWESOME Sink. Enter names it, Esc restores the suggestion; a dashed name is a guess."
    );
    var t = table(
      [
        ["machines", ""],
        ["makes", "name"],
        ["source", "name"],
        ["region", "name"],
        ["name", "name"],
        ["", ""],
      ],
      false
    );
    var tb = t.tBodies[0]!;
    (detect.showSkipped ? data.candidates : open).forEach(function (row) {
      candidateRow(tb, row);
    });
    var wrap = make("div", "dash-scroll");
    wrap.appendChild(t);
    card.appendChild(wrap);
  }
  hiddenNote(card, data);
  skippedToggle(card, data.candidates.length - open.length);
  note(card, "the clusters propose_factories finds, as of " + data.token + "; a name for a save written since then is refused, so detect again");
  body.appendChild(card);
}

function graphPath(source: string, subject: string, token?: string): `/api/factories/graph?${string}` {
  var q = source === "factory" ? "factory=" + encodeURIComponent(subject) : "candidate=" + encodeURIComponent(subject) + "&token=" + encodeURIComponent(token || "");
  return ("/api/factories/graph?" + q) as `/api/factories/graph?${string}`;
}

function openGraph(source: string, subject: string, token?: string): void {
  graphView.source = source;
  graphView.subject = subject;
  graphView.busy = true;
  graphView.error = "";
  graphView.data = null;
  graphView.drawn = null;
  render();
  get<FactoryGraphResponse>(graphPath(source, subject, token))
    .then(function (data) {
      graphView.data = data;
      graphView.drawn = drawGraph(data, nodeTip, function (node) {
        var box = node.bbox_m;
        if (box) {
          toMap(function () {
            showBox(box!);
          });
        }
      });
    })
    .catch(function (error) {
      graphView.error = friendly(error);
    })
    .then(function () {
      graphView.busy = false;
      render();
    });
}

function closeGraph(): void {
  graphView.source = "";
  graphView.data = null;
  graphView.drawn = null;
  render();
}

function nodeTip(node: GraphNode): string {
  var lines = [node.label + (node.detail ? " · " + node.detail : "")];
  if (node.kind === "group") {
    lines.push(statusLine(node) + (node.clock !== null ? " · clock " + Math.round(node.clock * 100) + "%" : ""));
    node.makes.forEach(function (f) {
      lines.push("makes " + count(Math.round(f.per_min * 10) / 10) + " " + f.name + "/min" + (f.to.length ? " → " + f.to.join(", ") : ""));
    });
    lines.push("click: show these machines on the map");
  }
  if (node.kind === "sink") lines.push("what reaches the AWESOME Sink is sunk, not a product");
  return lines.join("\n");
}

function statusLine(node: GraphNode): string {
  return node.running + " running · " + node.blocked + " blocked · " + node.stopped + " stopped";
}

function graphCard(parent: HTMLElement): void {
  var card = make("section", "dash-card dash-graph");
  var bar = make("div", "dash-title");
  var data = graphView.data;
  bar.appendChild(make("h2", "dash-h", "production graph" + (data ? " · " + data.title : "")));
  bar.appendChild(actionButton("close", "close the graph", closeGraph));
  card.appendChild(bar);
  if (graphView.busy) note(card, "drawing…");
  else if (graphView.error) note(card, "the graph could not be drawn: " + graphView.error);
  else if (graphView.drawn && data) {
    card.appendChild(graphView.drawn);
    note(
      card,
      "Recipe groups left to right, nameplate items/min apportioned by each producer's share. Red outline: a machine stopped; yellow: blocked. " +
        (data.buffers ? data.buffers + " box(es) sit between machines and are walked through. " : "") +
        "Scroll to zoom, drag to pan, double-click to reset, click a group to see it on the map."
    );
  }
  parent.appendChild(card);
}

export function renderFactory(body: HTMLElement, name: string): void {
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
    var now = renamedTo(name);
    if (now) {
      history.replaceState(null, "", hashFor("factories/" + now));
      state.dash = "factories/" + now;
      renderFactory(body, now);
      return;
    }
    note(body, "no factory named “" + name + "” in this save: it may have been renamed or forgotten since this link was made");
    return;
  }
  var head = make("div", "dash-title");
  var title = make("h1", "", row.name);
  head.appendChild(title);
  head.appendChild(
    renameButton(row.name, title, function (to) {
      history.replaceState(null, "", hashFor("factories/" + to));
      state.dash = "factories/" + to;
      render();
    })
  );
  head.appendChild(factoryMapButton(row));
  var shown = graphView.source === "factory" && graphView.subject === row.name;
  head.appendChild(
    actionButton(shown ? "hide graph" : "graph", "draw this factory's production graph", function () {
      if (shown) closeGraph();
      else openGraph("factory", row!.name);
    })
  );
  head.appendChild(
    button(
      "trace supply",
      function () {
        toMap(function () {
          startTrace("label:" + row!.name, "up");
        });
      },
      { title: "draw what feeds this factory on the map, with items and rates" }
    )
  );
  body.appendChild(head);
  if (shown) graphCard(body);
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
  type StateRow = FactoryHealthRow["states"][number];
  function toneOf(s: StateRow): string {
    var shade = tone(s.state);
    return shade === "bad" || shade === "blocked" ? shade : "";
  }
  states.appendChild(
    kitTable<StateRow>(
      [
        {
          key: "state",
          label: "state",
          render: function (s) {
            return s.state;
          },
        },
        {
          key: "machines",
          label: "machines",
          align: "right",
          render: function (s) {
            return count(s.count);
          },
        },
        {
          key: "bar",
          label: "",
          className: "bar",
          render: function (s) {
            var bar = make("div", "dash-hbar");
            var fill = make("span", "dash-mix-" + toneClass(tone(s.state)));
            fill.style.width = (s.count / biggest) * 100 + "%";
            bar.appendChild(fill);
            return bar;
          },
        },
      ],
      row.states,
      { rowClass: toneOf, caption: "machines by state" }
    )
  );
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

function detectAsked(): string {
  return choice("naming") + "|" + setting("fedOnly") + "|" + amount("minMachines");
}

function settingChanged(): void {
  var asked = detectAsked();
  if (detect.data && !detect.busy && asked !== detect.asked) {
    detect.asked = asked;
    runDetect(true);
  }
  render();
}

export function wireDetect(): void {
  detect.asked = detectAsked();
  onSetting(settingChanged);
}
