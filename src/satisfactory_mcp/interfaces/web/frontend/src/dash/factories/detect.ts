/* The Factories tab's unnamed-cluster card: detect the machines no factory covers, name them
 * one by one, undo a naming. */

import { get, latest, send } from "../../api/client";
import { appendNote, button, empty, error, fieldError, link, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { buildingCounts, count, flow } from "../../kit/format";
import { ringCandidate } from "../../map/tools/lasso";
import { showBox } from "../../map/map-highlight";
import { onSetting, settingChoice, settingNumber, settingOn } from "../../app/settings";
import { state } from "../../app/state";
import { fail, friendlyError, notify } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";
import { mapButton, requestRender } from "../actions";
import { graphView, openGraph, renderGraphCard } from "./graph-view";
import {
  factoryNameProblem,
  labelRefusal,
  labelsVersionToSend,
  NAME_MAX,
  recordLabelsVersion,
  refetchLabelledViews,
  staleWriteReason,
} from "./rename";

import type { CandidateRow, CandidatesResponse, Flow, ForgotResponse, NamedResponse } from "../../api/shapes";

const detect = {
  busy: false,
  failure: null as unknown,
  data: null as CandidatesResponse | null,
  world: "",
  epoch: -1,
  edits: {} as Record<number, string>,
  nameErrors: {} as Record<number, string>,
  skipped: {} as Record<number, boolean>,
  showSkipped: false,
  saving: -1,
  named: [] as NamedResponse[],
  showAll: false,
  settingsKey: "",
};

const SOURCE: Record<string, string> = { fed: "fed", "not fed": "not fed", transport: "via station" };

/* A new save drops the list, and runs detection again if it had run. */
export function resetDetectOnNewEpoch(): void {
  if (detect.epoch === state.epoch) return;
  const again = detect.busy || !!detect.data;
  detect.epoch = state.epoch;
  detect.data = null;
  detect.busy = false;
  detect.failure = null;
  detect.edits = {};
  detect.nameErrors = {};
  detect.skipped = {};
  if (detect.world !== state.world) detect.named = [];
  if (again) startDetect(false);
}

function detectPath(): `/api/factories/candidates?${string}` {
  const fedOnly = detect.showAll ? false : settingOn("fedOnly");
  const least = detect.showAll ? 1 : Math.max(1, settingNumber("minMachines"));
  return (
    "/api/factories/candidates?style=" +
    encodeURIComponent(settingChoice("naming")) +
    "&fed_only=" +
    fedOnly +
    "&min_machines=" +
    least
  ) as `/api/factories/candidates?${string}`;
}

function startDetect(keepEdits: boolean): void {
  const ticket = latest("detect");
  const world = state.world;
  const epoch = state.epoch;
  detect.busy = true;
  detect.failure = null;
  detect.settingsKey = detectSettingsKey();
  detect.epoch = epoch;
  get<CandidatesResponse>(detectPath())
    .then(function (data) {
      if (!ticket.fresh()) return;
      data.version = labelsVersionToSend(data.version);
      detect.data = data;
      detect.world = world;
      if (!keepEdits) {
        detect.edits = {};
        detect.skipped = {};
      }
      detect.nameErrors = {};
    })
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      detect.data = null;
      detect.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      detect.busy = false;
      requestRender();
    });
}

function runDetect(keepEdits?: boolean): void {
  startDetect(!!keepEdits);
  requestRender();
}

function chosenName(row: CandidateRow): string {
  const edited = detect.edits[row.index];
  return (edited === undefined ? row.suggested_name : edited).trim();
}

function setNameError(row: CandidateRow, message: string): void {
  if (message) detect.nameErrors[row.index] = message;
  else delete detect.nameErrors[row.index];
  requestRender();
}

function nameCandidate(row: CandidateRow): void {
  const data = detect.data;
  if (!data || detect.saving >= 0) return;
  const name = chosenName(row);
  const problem = factoryNameProblem(name);
  if (problem) {
    setNameError(row, problem);
    return;
  }
  delete detect.nameErrors[row.index];
  detect.saving = row.index;
  requestRender();
  send<NamedResponse>("POST", "/api/labels", {
    name: name,
    proposal: row.index,
    as_of: data.token,
    version: labelsVersionToSend(data.version),
  })
    .then(function (reply) {
      recordLabelsVersion(reply.version);
      if (detect.data) {
        detect.data.version = reply.version;
        detect.data.candidates = detect.data.candidates.filter(function (candidate) {
          return candidate.index !== row.index;
        });
      }
      detect.named.unshift(reply);
      notify("named “" + reply.name + "”, " + counted(reply.machines, "machine"));
      reply.overlaps.forEach(fail);
      refetchLabelledViews();
    })
    .catch(function (failure) {
      const why = labelRefusal(failure);
      if (why === "name_taken") detect.nameErrors[row.index] = "“" + name + "” is already a factory name";
      else if (why === "bad") detect.nameErrors[row.index] = friendlyError(failure);
      else if (why === "stale" || why === "pin") {
        fail(staleWriteReason(why) + ", so “" + name + "” was not written; the list is fresh now, name it again");
        refetchLabelledViews();
        runDetect(why === "stale");
      } else fail("naming “" + name + "”: " + friendlyError(failure));
    })
    .then(function () {
      detect.saving = -1;
      requestRender();
    });
}

function forgetNamed(reply: NamedResponse): void {
  const version = labelsVersionToSend(detect.data ? detect.data.version : reply.version);
  send<ForgotResponse>("DELETE", "/api/labels/{name}", undefined, reply.name, "version=" + version)
    .then(function (gone) {
      recordLabelsVersion(gone.version);
      detect.named = detect.named.filter(function (named) {
        return named !== reply;
      });
      notify("forgot “" + reply.name + "”");
    })
    .catch(function (failure) {
      if (labelRefusal(failure) === "stale") {
        fail(staleWriteReason("stale") + ", so “" + reply.name + "” was kept; they are reloaded now, undo again");
      } else fail("forgetting “" + reply.name + "”: " + friendlyError(failure));
    })
    .then(function () {
      refetchLabelledViews();
      runDetect(true);
    });
}

function flowsText(list: Flow[]): string {
  return list
    .map(function (item) {
      return flow(item.name, item.per_min);
    })
    .join(" · ");
}

function buildingsText(row: CandidateRow, joiner: string): string {
  return buildingCounts(row.buildings, joiner);
}

function makesCell(row: CandidateRow): HTMLElement {
  const box = make("div", "dash-makes");
  box.appendChild(make("span", "", row.products.length ? flowsText(row.products) : buildingsText(row, " · ") || "–"));
  if (row.intermediates.length) box.appendChild(make("span", "dash-sub", "via " + flowsText(row.intermediates)));
  if (row.sunk.length) box.appendChild(make("span", "dash-sub dash-sunk", "sunk " + flowsText(row.sunk)));
  if (row.unrouted.length) box.appendChild(make("span", "dash-sub", "goes nowhere " + flowsText(row.unrouted)));
  if (row.inputs.length) box.appendChild(make("span", "dash-sub", "in " + flowsText(row.inputs)));
  box.title = buildingsText(row, ", ");
  return box;
}

function nameField(row: CandidateRow): HTMLElement {
  const cell = make("div", "dash-field");
  const input = make("input", "dash-name" + (row.confident ? "" : " guess"));
  input.type = "text";
  input.maxLength = NAME_MAX;
  const edited = detect.edits[row.index];
  input.value = edited === undefined ? row.suggested_name : edited;
  input.setAttribute("data-candidate", String(row.index));
  input.setAttribute("aria-label", "name for the unnamed cluster of " + counted(row.machines, "machine"));
  if (!row.confident) input.title = "no clear end product, so this name is a guess";
  input.spellcheck = false;
  input.oninput = function () {
    detect.edits[row.index] = input.value;
    if (detect.nameErrors[row.index]) {
      delete detect.nameErrors[row.index];
      fieldError(input, "");
    }
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") nameCandidate(row);
    if (event.key === "Escape") {
      delete detect.edits[row.index];
      delete detect.nameErrors[row.index];
      input.value = row.suggested_name;
      fieldError(input, "");
    }
  };
  cell.appendChild(input);
  if (detect.nameErrors[row.index]) fieldError(input, detect.nameErrors[row.index]!);
  return cell;
}

function machinesCell(row: CandidateRow): HTMLElement {
  const span = make("span", "", count(row.machines));
  span.appendChild(make("span", "dt-unit", row.machines === 1 ? " machine" : " machines"));
  return span;
}

function candidateTable(rows: CandidateRow[]): HTMLElement {
  const wrap = table<CandidateRow>(
    [
      { key: "machines", label: "machines", align: "right", className: "dt-n", render: machinesCell },
      {
        key: "makes",
        label: "makes",
        className: "dt-makes",
        title:
          "items/min at nameplate: products reach a box or leave the cluster; via is made and used inside; sunk goes to the AWESOME Sink; goes nowhere ends on an open belt or pipe; in is brought in",
        render: makesCell,
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
      { key: "acts", label: "", align: "right", className: "dt-acts", render: candidateActions },
    ],
    rows,
    {
      rowClass: function (row) {
        return detect.skipped[row.index] ? "dash-skipped" : "";
      },
      caption: WORDS.unnamedClusters,
    }
  );
  wrap.classList.add("dash-detect");
  return wrap;
}

function candidateActions(row: CandidateRow): HTMLElement {
  const skipped = !!detect.skipped[row.index];
  const acts = make("span", "dash-acts");
  const box = row.bbox_m;
  acts.appendChild(
    box
      ? mapButton("fly the map to this " + WORDS.unnamedCluster + ", outline it and ring its machines", function () {
          showBox(box);
          if (detect.data) ringCandidate(row.selector, detect.data.token, chosenName(row));
        })
      : make("span", "dash-muted", "–")
  );
  acts.appendChild(
    button(
      "graph",
      function () {
        const data = detect.data;
        if (data) openGraph("candidate", String(row.index), chosenName(row) + " (" + WORDS.unnamedCluster + ")", data.token);
      },
      { title: "draw the production graph of this " + WORDS.unnamedCluster }
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
        requestRender();
      },
      { title: skipped ? "offer it again" : "leave it unnamed for now" }
    )
  );
  return acts;
}

function namedList(card: HTMLElement): void {
  detect.named.forEach(function (reply) {
    const line = make("p", "dash-note dash-filters");
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
  const line = make("p", "dash-note dash-filters");
  const parts: string[] = [];
  if (detect.showAll) appendFiltersSentence(line, "every " + WORDS.unnamedCluster + ", ", " off");
  else {
    if (data.hidden.not_fed) parts.push(count(data.hidden.not_fed) + " not fed");
    if (data.hidden.small) parts.push(count(data.hidden.small) + " below " + counted(data.min_machines, "machine"));
    if (parts.length) appendFiltersSentence(line, count(data.hidden.not_fed + data.hidden.small) + " hidden by the ", ": " + parts.join(", "));
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
        requestRender();
      })
    );
  }
  if (line.childNodes.length) card.appendChild(line);
}

function appendFiltersSentence(line: HTMLElement, before: string, after: string): void {
  const span = make("span", "", before);
  span.appendChild(link("settings", "filters"));
  span.appendChild(document.createTextNode(after));
  line.appendChild(span);
}

function detectLabel(): string {
  if (detect.busy) return "detecting…";
  return detect.data ? "detect again" : "detect";
}

export function renderDetect(body: HTMLElement): void {
  const data = detect.data;
  const card = make("section", "dash-card");
  const bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", WORDS.unnamedClusters));
  bar.appendChild(
    button(
      detectLabel(),
      function () {
        runDetect();
      },
      { title: "find the machines no factory covers", disabled: detect.busy }
    )
  );
  card.appendChild(bar);
  if (detect.failure) error(card, WORDS.unnamedClusters, detect.failure, runDetect);
  namedList(card);
  if (graphView.source === "candidate") renderGraphCard(card);
  if (!data) {
    if (!detect.failure) appendNote(card, "finds the machines no factory covers yet and suggests a name for each");
    body.appendChild(card);
    return;
  }
  const open = data.candidates.filter(function (candidate) {
    return !detect.skipped[candidate.index];
  });
  const rows = detect.showSkipped ? data.candidates : open;
  if (!rows.length) {
    empty(card, data.candidates.length ? "every " + WORDS.unnamedCluster + " here is skipped" : "no " + WORDS.unnamedCluster + " left to show");
  } else {
    appendNote(card, counted(open.length, WORDS.unnamedCluster) + ", largest first");
    card.appendChild(candidateTable(rows));
  }
  filterLine(card, data, data.candidates.length - open.length);
  body.appendChild(card);
}

function detectSettingsKey(): string {
  return settingChoice("naming") + "|" + settingOn("fedOnly") + "|" + settingNumber("minMachines") + "|" + detect.showAll;
}

function settingChanged(): void {
  if ((detect.data || detect.busy) && detectSettingsKey() !== detect.settingsKey) runDetect(true);
  else requestRender();
}

export function wireDetect(): void {
  detect.settingsKey = detectSettingsKey();
  onSetting(settingChanged);
}
