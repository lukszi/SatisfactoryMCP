/* The dashboard's Factories tab: the named factories, unnamed-cluster detection and the
 * production graph, addressed as `dash=factories[/<name>]`. */

import { adviceCard } from "../../chat/advice";
import { get, latest, send } from "../../api/client";
import { button, empty, error, fieldError, heading, link, loading, note, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, flow, mw, pct, spoken } from "../../kit/format";
import { drawGraph, graphCard as graphFrame, GRAPH_HINT, stateLine } from "../graph";
import { loadOne } from "../../app/load";
import { ringCandidate, startLasso } from "../../map/tools/lasso";
import { hashFor } from "../../map/map";
import { showBox } from "../../map/map-highlight";
import { vitals } from "../../app/vitals";
import { blankOrLong, NAME_MAX, newest, onRenamed, refreshLabels, refusal, renamedTo, wrote } from "./rename";
import { amount, choice, onSetting, setting } from "../../app/settings";
import { state } from "../../app/state";
import { actionTone, stateSets, tone, toneClass } from "../machine-states";
import { fail, friendly, note as said } from "../../kit/toast";
import { startTrace } from "../../map/tools/trace";
import { counted, W } from "../../kit/words";
import { factoryMapButton, go, mapButton, pointButton, renameButton, render, sort, toMap } from "../shell";
import { actionable, issueCount, issueGroups, issueTable, mixBar, mixOf } from "../machine-health";
import { aspectTabs, factoryAddress, factoryDash, factoryPinButton, renderAspect } from "./factory-detail";

import type { Column } from "../../kit/dashkit";
import type {
  CandidateRow,
  CandidatesResponse,
  FactoryGraphResponse,
  FactoryHealthRow,
  Flow,
  ForgotResponse,
  GraphNode,
  NamedResponse,
} from "../../api/shapes";

var detect = {
  busy: false,
  failure: null as unknown,
  data: null as CandidatesResponse | null,
  world: "",
  epoch: -1,
  edits: {} as Record<number, string>,
  invalid: {} as Record<number, string>,
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
  title: "",
  token: "",
  epoch: -1,
  busy: false,
  failure: null as unknown,
  data: null as FactoryGraphResponse | null,
  drawn: null as HTMLElement | null,
};

function settle(): void {
  if (graphView.source && graphView.epoch !== state.epoch) resetGraph();
  if (detect.epoch === state.epoch) return;
  var again = detect.busy || !!detect.data;
  detect.epoch = state.epoch;
  detect.data = null;
  detect.busy = false;
  detect.failure = null;
  detect.edits = {};
  detect.invalid = {};
  detect.skipped = {};
  if (detect.world !== state.world) detect.named = [];
  if (again) startDetect(false);
}

function sortValue(row: FactoryHealthRow, key: string): number | string {
  if (key === "name") return row.name.toLowerCase();
  if (key === "uptime") return row.uptime === null ? -1 : row.uptime;
  var value = (row as unknown as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

function counter(key: keyof FactoryHealthRow, label: string, flag: boolean, title: string): Column<FactoryHealthRow> {
  return {
    key: key,
    label: label,
    align: "right",
    title: title,
    sort: function (row) {
      return sortValue(row, key);
    },
    tone: flag
      ? function (row) {
          return row[key] ? "bad" : "";
        }
      : undefined,
    render: function (row) {
      return count(row[key] as number);
    },
  };
}

function needColumn(): Column<FactoryHealthRow> {
  var column = counter("actionable", W.needAction, false, W.needAction + ": " + spoken(actionable(), "or"));
  column.tone = function (row) {
    return actionTone(row.states);
  };
  return column;
}

function factoryColumns(): Column<FactoryHealthRow>[] {
  return [
    {
      key: "name",
      label: "factory",
      sort: function (row) {
        return sortValue(row, "name");
      },
      render: function (r) {
        var named = make("span", "dash-named");
        named.appendChild(link("factories/" + r.name, r.name));
        named.appendChild(
          renameButton(r.name, named, function () {
            render();
          })
        );
        return named;
      },
    },
    {
      key: "alive",
      label: "machines",
      align: "right",
      title: "machines standing, of the anchors the label holds",
      sort: function (row) {
        return sortValue(row, "alive");
      },
      render: function (r) {
        return count(r.alive) + (r.alive === r.anchors ? "" : " of " + count(r.anchors));
      },
    },
    {
      key: "uptime",
      label: "uptime",
      align: "right",
      title: "each machine's last 300 s, averaged; a blocked machine makes nothing, so a blocked factory reads near 0%",
      sort: function (row) {
        return sortValue(row, "uptime");
      },
      render: function (r) {
        return pct(r.uptime);
      },
    },
    needColumn(),
    counter("attention", W.notRunning, false, "every machine that is not " + spoken(stateSets().ok, "or")),
    counter("unwired", W.noWire, true, W.powerProblems + ": machines with " + W.noWire),
    counter("no_generator", W.noGenerator, true, W.powerProblems + ": machines on a circuit with " + W.noGenerator),
    {
      key: "measured_mw",
      label: W.measuredDraw,
      align: "right",
      sort: function (row) {
        return sortValue(row, "measured_mw");
      },
      render: function (r) {
        return mw(r.measured_mw);
      },
    },
    {
      key: "nameplate_mw",
      label: W.nameplateDraw,
      align: "right",
      sort: function (row) {
        return sortValue(row, "nameplate_mw");
      },
      render: function (r) {
        return mw(r.nameplate_mw);
      },
    },
    {
      key: "mix",
      label: "state mix",
      className: "mix",
      render: function (r) {
        return mixBar(mixOf([r]));
      },
    },
    { key: "map", label: "", align: "right", render: factoryMapButton },
  ];
}

function healthState(body: HTMLElement): boolean {
  var v = vitals();
  if (v.health) return true;
  if (v.healthError) {
    error(body, "factory health", v.healthError, function () {
      loadOne("/api/factories/health");
    });
  } else loading(body, "factory health");
  return false;
}

export function renderFactories(body: HTMLElement): void {
  settle();
  if (!healthState(body)) return;
  renderDetect(body);
  var rows = vitals().health!.factories.slice();
  if (!rows.length) {
    empty(body, "no factories named yet", "detect them above, or ask chat to name one");
    return;
  }
  heading(body, counted(rows.length, "named factory", "named factories"));
  body.appendChild(
    table<FactoryHealthRow>(factoryColumns(), rows, {
      sort: sort,
      onSort: render,
      onRow: function (r) {
        go("factories/" + r.name);
      },
      caption: "named factories",
    })
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

function startDetect(keep: boolean): void {
  var ticket = latest("detect");
  var world = state.world;
  var epoch = state.epoch;
  detect.busy = true;
  detect.failure = null;
  detect.asked = detectAsked();
  detect.epoch = epoch;
  get<CandidatesResponse>(detectPath())
    .then(function (data) {
      if (!ticket.fresh()) return;
      data.version = newest(data.version);
      detect.data = data;
      detect.world = world;
      if (!keep) {
        detect.edits = {};
        detect.skipped = {};
      }
      detect.invalid = {};
    })
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      detect.data = null;
      detect.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      detect.busy = false;
      render();
    });
}

function runDetect(keep?: boolean): void {
  startDetect(!!keep);
  render();
}

function chosenName(row: CandidateRow): string {
  var edited = detect.edits[row.index];
  return (edited === undefined ? row.suggested_name : edited).trim();
}

function invalid(row: CandidateRow, message: string): void {
  if (message) detect.invalid[row.index] = message;
  else delete detect.invalid[row.index];
  render();
}

function nameCandidate(row: CandidateRow): void {
  var data = detect.data;
  if (!data || detect.saving >= 0) return;
  var name = chosenName(row);
  var problem = blankOrLong(name);
  if (problem) {
    invalid(row, problem);
    return;
  }
  delete detect.invalid[row.index];
  detect.saving = row.index;
  render();
  send<NamedResponse>("POST", "/api/labels", {
    name: name,
    proposal: row.index,
    as_of: data.token,
    version: newest(data.version),
  })
    .then(function (reply) {
      wrote(reply.version);
      if (detect.data) {
        detect.data.version = reply.version;
        detect.data.candidates = detect.data.candidates.filter(function (c) {
          return c.index !== row.index;
        });
      }
      detect.named.unshift(reply);
      said("named “" + reply.name + "”, " + counted(reply.machines, "machine"));
      reply.overlaps.forEach(fail);
      refreshLabels();
    })
    .catch(function (failure) {
      var why = refusal(failure);
      if (why === "name_taken") detect.invalid[row.index] = "“" + name + "” is already a factory name";
      else if (why === "bad") detect.invalid[row.index] = friendly(failure);
      else if (why === "stale" || why === "pin") {
        fail(
          (why === "pin" ? "a newer save was written" : "factory names changed elsewhere") +
            ", so “" + name + "” was not written; the list is fresh now, name it again"
        );
        refreshLabels();
        runDetect(why === "stale");
      } else fail("naming “" + name + "”: " + friendly(failure));
    })
    .then(function () {
      detect.saving = -1;
      render();
    });
}

function forgetNamed(reply: NamedResponse): void {
  var version = newest(detect.data ? detect.data.version : reply.version);
  send<ForgotResponse>("DELETE", "/api/labels/{name}", undefined, reply.name, "version=" + version)
    .then(function (gone) {
      wrote(gone.version);
      detect.named = detect.named.filter(function (n) {
        return n !== reply;
      });
      said("forgot “" + reply.name + "”");
    })
    .catch(function (failure) {
      if (refusal(failure) === "stale") {
        fail("factory names changed elsewhere, so “" + reply.name + "” was kept; they are reloaded now, undo again");
      } else fail("forgetting “" + reply.name + "”: " + friendly(failure));
    })
    .then(function () {
      refreshLabels();
      runDetect(true);
    });
}

function flows(list: Flow[]): string {
  return list
    .map(function (f) {
      return flow(f.name, f.per_min);
    })
    .join(" · ");
}

function buildings(row: CandidateRow, joiner: string): string {
  return row.buildings
    .map(function (b) {
      return b.count + "× " + b.name;
    })
    .join(joiner);
}

function makes(row: CandidateRow): HTMLElement {
  var box = make("div", "dash-makes");
  box.appendChild(make("span", "", row.products.length ? flows(row.products) : buildings(row, " · ") || "–"));
  if (row.intermediates.length) box.appendChild(make("span", "dash-sub", "via " + flows(row.intermediates)));
  if (row.sunk.length) box.appendChild(make("span", "dash-sub dash-sunk", "sunk " + flows(row.sunk)));
  if (row.unrouted.length) box.appendChild(make("span", "dash-sub", "goes nowhere " + flows(row.unrouted)));
  if (row.inputs.length) box.appendChild(make("span", "dash-sub", "in " + flows(row.inputs)));
  box.title = buildings(row, ", ");
  return box;
}

var SOURCE: Record<string, string> = { fed: "fed", "not fed": "not fed", transport: "via station" };

function nameField(row: CandidateRow): HTMLElement {
  var cell = make("div", "dash-field");
  var input = make("input", "dash-name" + (row.confident ? "" : " guess"));
  input.type = "text";
  input.maxLength = NAME_MAX;
  var edited = detect.edits[row.index];
  input.value = edited === undefined ? row.suggested_name : edited;
  input.setAttribute("data-candidate", String(row.index));
  input.setAttribute("aria-label", "name for the unnamed cluster of " + counted(row.machines, "machine"));
  if (!row.confident) input.title = "no clear end product, so this name is a guess";
  input.spellcheck = false;
  input.oninput = function () {
    detect.edits[row.index] = input.value;
    if (detect.invalid[row.index]) {
      delete detect.invalid[row.index];
      fieldError(input, "");
    }
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") nameCandidate(row);
    if (event.key === "Escape") {
      delete detect.edits[row.index];
      delete detect.invalid[row.index];
      input.value = row.suggested_name;
      fieldError(input, "");
    }
  };
  cell.appendChild(input);
  if (detect.invalid[row.index]) fieldError(input, detect.invalid[row.index]!);
  return cell;
}

function machinesCell(row: CandidateRow): HTMLElement {
  var span = make("span", "", count(row.machines));
  span.appendChild(make("span", "dt-unit", row.machines === 1 ? " machine" : " machines"));
  return span;
}

function candidateTable(rows: CandidateRow[]): HTMLElement {
  var wrap = table<CandidateRow>(
    [
      { key: "machines", label: "machines", align: "right", className: "dt-n", render: machinesCell },
      {
        key: "makes",
        label: "makes",
        className: "dt-makes",
        title:
          "items/min at nameplate: products reach a box or leave the cluster; via is made and used inside; sunk goes to the AWESOME Sink; goes nowhere ends on an open belt or pipe; in is brought in",
        render: makes,
      },
      {
        key: "source",
        label: "source",
        className: "dt-src",
        tone: function (row) {
          return row.fed === "not fed" ? "dash-muted" : "";
        },
        render: function (row) {
          return SOURCE[row.fed] || row.fed;
        },
      },
      {
        key: "region",
        label: "region",
        className: "dt-reg",
        tone: function (row) {
          return row.region ? "" : "dash-muted";
        },
        render: function (row) {
          return row.region || "–";
        },
      },
      {
        key: "name",
        label: "name",
        className: "name",
        title: "Enter saves the name, Esc restores the suggestion; a dashed border marks a guess",
        render: nameField,
      },
      { key: "acts", label: "", align: "right", className: "dt-acts", render: candidateActs },
    ],
    rows,
    {
      rowClass: function (row) {
        return detect.skipped[row.index] ? "dash-skipped" : "";
      },
      caption: W.unnamedClusters,
    }
  );
  wrap.classList.add("dash-detect");
  return wrap;
}

function candidateActs(row: CandidateRow): HTMLElement {
  var skipped = !!detect.skipped[row.index];
  var acts = make("span", "dash-acts");
  var box = row.bbox_m;
  acts.appendChild(
    box
      ? mapButton("fly the map to this " + W.unnamedCluster + ", outline it and ring its machines", function () {
          showBox(box!);
          if (detect.data) ringCandidate(row.selector, detect.data.token, chosenName(row));
        })
      : make("span", "dash-muted", "–")
  );
  acts.appendChild(
    button(
      "graph",
      function () {
        var data = detect.data;
        if (data) openGraph("candidate", String(row.index), chosenName(row) + " (" + W.unnamedCluster + ")", data.token);
      },
      { title: "draw the production graph of this " + W.unnamedCluster }
    )
  );
  acts.appendChild(
    button(
      detect.saving === row.index ? "naming…" : "name",
      function () {
        nameCandidate(row);
      },
      { title: "save this name as a factory", disabled: detect.saving >= 0 }
    )
  );
  acts.appendChild(
    button(
      skipped ? "unskip" : "skip",
      function () {
        if (skipped) delete detect.skipped[row.index];
        else detect.skipped[row.index] = true;
        render();
      },
      { title: skipped ? "offer it again" : "leave it unnamed for now" }
    )
  );
  return acts;
}

function namedList(card: HTMLElement): void {
  detect.named.forEach(function (reply) {
    var line = make("p", "dash-note dash-filters");
    line.appendChild(make("span", "", "named “" + reply.name + "”, " + counted(reply.machines, "machine")));
    line.appendChild(
      button(
        "undo",
        function () {
          forgetNamed(reply);
        },
        { title: "forget this label again", label: "undo naming " + reply.name }
      )
    );
    card.appendChild(line);
  });
}

function filterLine(card: HTMLElement, data: CandidatesResponse, skipped: number): void {
  var line = make("p", "dash-note dash-filters");
  var parts: string[] = [];
  if (detect.showAll) sentence(line, "every " + W.unnamedCluster + ", ", " off");
  else {
    if (data.hidden.not_fed) parts.push(count(data.hidden.not_fed) + " not fed");
    if (data.hidden.small) parts.push(count(data.hidden.small) + " below " + counted(data.min_machines, "machine"));
    if (parts.length) sentence(line, count(data.hidden.not_fed + data.hidden.small) + " hidden by the ", ": " + parts.join(", "));
  }
  if (detect.showAll) {
    line.appendChild(
      button(
        "use filters",
        function () {
          detect.showAll = false;
          runDetect(true);
        },
        { title: "hide what Settings says to hide" }
      )
    );
  } else if (parts.length) {
    line.appendChild(
      button(
        "show all",
        function () {
          detect.showAll = true;
          runDetect(true);
        },
        { title: "list every unnamed cluster, filters off" }
      )
    );
  }
  if (skipped) {
    line.appendChild(
      button(detect.showSkipped ? "hide skipped" : "show " + count(skipped) + " skipped", function () {
        detect.showSkipped = !detect.showSkipped;
        render();
      })
    );
  }
  if (line.childNodes.length) card.appendChild(line);
}

function sentence(line: HTMLElement, before: string, after: string): void {
  var span = make("span", "", before);
  span.appendChild(link("settings", "filters"));
  span.appendChild(document.createTextNode(after));
  line.appendChild(span);
}

function renderDetect(body: HTMLElement): void {
  var data = detect.data;
  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", W.unnamedClusters));
  bar.appendChild(
    button(
      detect.busy ? "detecting…" : data ? "detect again" : "detect",
      function () {
        runDetect();
      },
      { title: "find the machines no factory covers", disabled: detect.busy }
    )
  );
  card.appendChild(bar);
  if (detect.failure) error(card, W.unnamedClusters, detect.failure, runDetect);
  namedList(card);
  if (graphView.source === "candidate") graphCard(card);
  if (!data) {
    if (!detect.failure) note(card, "finds the machines no factory covers yet and suggests a name for each");
    body.appendChild(card);
    return;
  }
  var open = data.candidates.filter(function (c) {
    return !detect.skipped[c.index];
  });
  var rows = detect.showSkipped ? data.candidates : open;
  if (!rows.length) {
    empty(card, data.candidates.length ? "every " + W.unnamedCluster + " here is skipped" : "no " + W.unnamedCluster + " left to show");
  } else {
    note(card, counted(open.length, W.unnamedCluster) + ", largest first");
    card.appendChild(candidateTable(rows));
  }
  filterLine(card, data, data.candidates.length - open.length);
  body.appendChild(card);
}

function graphPath(source: string, subject: string, token?: string): `/api/factories/graph?${string}` {
  var q =
    source === "factory"
      ? "factory=" + encodeURIComponent(subject)
      : "candidate=" + encodeURIComponent("proposal:" + subject) + "&token=" + encodeURIComponent(token || "");
  return ("/api/factories/graph?" + q) as `/api/factories/graph?${string}`;
}

function resetGraph(): void {
  latest("graph");
  graphView.source = "";
  graphView.subject = "";
  graphView.title = "";
  graphView.busy = false;
  graphView.failure = null;
  graphView.data = null;
  graphView.drawn = null;
}

function openGraph(source: string, subject: string, title: string, token?: string): void {
  var ticket = latest("graph");
  graphView.source = source;
  graphView.subject = subject;
  graphView.title = title;
  graphView.token = token || "";
  graphView.epoch = state.epoch;
  graphView.busy = true;
  graphView.failure = null;
  graphView.data = null;
  graphView.drawn = null;
  render();
  get<FactoryGraphResponse>(graphPath(source, subject, token))
    .then(function (data) {
      if (!ticket.fresh()) return;
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
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      graphView.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      graphView.busy = false;
      render();
    });
}

function closeGraph(): void {
  resetGraph();
  render();
}

function nodeTip(node: GraphNode): string {
  var lines = [node.label + (node.detail ? " · " + node.detail : "")];
  if (node.kind === "group") {
    lines.push(stateLine(node) + (node.clock !== null ? " · clock " + pct(node.clock) : ""));
    node.makes.forEach(function (f) {
      lines.push("makes " + flow(f.name, f.per_min) + (f.to.length ? " → " + f.to.join(", ") : ""));
    });
    lines.push("click: show these machines on the map");
  }
  if (node.kind === "sink") lines.push("what reaches the AWESOME Sink is sunk, not a product");
  return lines.join("\n");
}

function graphCard(parent: HTMLElement): void {
  var data = graphView.data;
  var card = graphFrame("production graph · " + graphView.title, true, graphView.source === "candidate" ? closeGraph : undefined);
  if (graphView.busy) loading(card, "the graph");
  else if (graphView.failure) {
    var again = { source: graphView.source, subject: graphView.subject, title: graphView.title, token: graphView.token };
    error(card, "the graph", graphView.failure, function () {
      openGraph(again.source, again.subject, again.title, again.token);
    });
  } else if (graphView.drawn && data) {
    card.appendChild(graphView.drawn);
    var lead = "nameplate rates split over each item's producers by share" + (data.buffers ? " · " + count(data.buffers) + " boxes walked through" : "");
    note(card, lead + " · " + GRAPH_HINT + ", click a group for the map");
  }
  parent.appendChild(card);
}

function worstList(parent: HTMLElement, row: FactoryHealthRow): void {
  var section = make("section", "dash-card");
  heading(section, W.needAction);
  var found = issueGroups([row]);
  if (!found.length) {
    empty(
      section,
      "none " + W.needAction,
      row.attention ? counted(row.attention, "machine") + " " + W.notRunning : ""
    );
    parent.appendChild(section);
    return;
  }
  section.appendChild(
    issueTable(
      found,
      function (g) {
        return pointButton(g.issues[0]!, "show " + g.what + " on the map");
      },
      false
    )
  );
  var listed = issueCount(found);
  if (listed < row.actionable) note(section, "showing " + count(listed) + " of " + count(row.actionable));
  parent.appendChild(section);
}

function statesTable(parent: HTMLElement, row: FactoryHealthRow): void {
  var section = make("section", "dash-card");
  heading(section, "machines by state");
  var biggest = 1;
  row.states.forEach(function (s) {
    biggest = Math.max(biggest, s.count);
  });
  type StateRow = FactoryHealthRow["states"][number];
  section.appendChild(
    table<StateRow>(
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
      {
        rowClass: function (s) {
          var shade = tone(s.state);
          return shade === "ok" ? "" : toneClass(shade);
        },
        caption: "machines by state",
      }
    )
  );
  parent.appendChild(section);
}

export function renderFactory(body: HTMLElement, subject: string): void {
  settle();
  var v = vitals();
  var at = factoryAddress(subject, function (whole) {
    return !!v.health && v.health.factories.some(function (r) {
      return r.name === whole;
    });
  });
  var name = at.name;
  var aspect = at.aspect;
  var row = v.health
    ? v.health.factories.filter(function (r) {
        return r.name === name;
      })[0]
    : undefined;
  if (v.health && !row) {
    var now = renamedTo(name);
    if (now) {
      history.replaceState(null, "", hashFor(factoryDash(now, aspect)));
      state.dash = factoryDash(now, aspect);
      renderFactory(body, factoryDash(now, aspect).slice("factories/".length));
      return;
    }
  }
  body.appendChild(link("factories", "‹ all factories", "dash-back"));
  if (!healthState(body)) return;
  if (!row) {
    empty(body, "no factory named “" + name + "” in this world", "it may have been renamed or forgotten; pick one from the list");
    return;
  }
  var head = make("div", "dash-title");
  var title = make("h1", "", row.name);
  head.appendChild(title);
  head.appendChild(
    renameButton(row.name, title, function (to) {
      if (state.dash !== factoryDash(row!.name, aspect)) {
        render();
        return;
      }
      history.replaceState(null, "", hashFor(factoryDash(to, aspect)));
      state.dash = factoryDash(to, aspect);
      render();
    })
  );
  head.appendChild(factoryMapButton(row));
  var shown = graphView.source === "factory" && graphView.subject === row.name;
  var toggle = button(
    shown ? "hide graph" : "graph",
    function () {
      if (shown) closeGraph();
      else openGraph("factory", row!.name, row!.name);
    },
    { title: "draw this factory's production graph" }
  );
  toggle.setAttribute("data-ctl", "graph");
  toggle.setAttribute("aria-expanded", String(shown));
  head.appendChild(toggle);
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
  head.appendChild(
    button(
      "amend on map",
      function () {
        toMap(function () {
          startLasso(row!.name);
        });
      },
      { title: "draw around machines on the map to add them to this factory or remove them" }
    )
  );
  head.appendChild(factoryPinButton(row.name));
  body.appendChild(head);
  if (shown) graphCard(body);
  body.appendChild(aspectTabs(row.name, aspect));
  if (aspect) {
    renderAspect(body, row.name, aspect);
    return;
  }
  if (row.review) note(body, "label " + row.review + ": " + row.alive + " of " + row.anchors + " anchors still stand");
  var power = row.unwired + row.no_generator;
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("machines", count(row.machines), pct(row.uptime) + " mean uptime"));
  var need = tile(W.needAction, count(row.actionable), count(row.attention) + " " + W.notRunning);
  var shade = actionTone(row.states);
  if (shade) need.classList.add(shade);
  tiles.appendChild(need);
  tiles.appendChild(tile(W.measuredDraw, mw(row.measured_mw), mw(row.nameplate_mw) + " nameplate"));
  tiles.appendChild(
    tile(
      W.powerProblems,
      count(power),
      power ? count(row.unwired) + " " + W.noWire + " · " + count(row.no_generator) + " " + W.noGenerator : "none",
      power > 0
    )
  );
  body.appendChild(tiles);
  adviceCard(body, { factory: row.name });

  var split = make("div", "dash-split");
  statesTable(split, row);
  worstList(split, row);
  body.appendChild(split);
}

function detectAsked(): string {
  return choice("naming") + "|" + setting("fedOnly") + "|" + amount("minMachines") + "|" + detect.showAll;
}

function settingChanged(): void {
  if ((detect.data || detect.busy) && detectAsked() !== detect.asked) runDetect(true);
  else render();
}

export function wireDetect(): void {
  detect.asked = detectAsked();
  onSetting(settingChanged);
}

onRenamed(function () {
  if (state.dash.indexOf("factories/") === 0) render();
});
