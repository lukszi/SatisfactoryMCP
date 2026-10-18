/* The Factories page's unnamed-cluster block: detect the machines no factory covers, name them
 * one by one, undo a naming, skip, and draw one cluster's graph. Compact rows on the kit. */

import { get, latest, send } from "../../api/client";
import { onSetting, settingChoice, settingNumber, settingOn } from "../../app/settings";
import { state } from "../../app/state";
import { count, perMin } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { openMenu } from "../../kit/overlay/menu";
import { btn, busyBtn, moreBtn } from "../../kit/ui/controls";
import { actSlots, entry, list } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH, emptyLine, note, toolrow } from "../../kit/ui/text";
import { fail, friendlyError, notify } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";
import { hashFor } from "../../map/map";
import { showBox } from "../../map/map-highlight";
import { ringCandidate } from "../../map/tools/lasso";
import { leaveDashThen, requestRender } from "../actions";
import { pending } from "./data";
import { candidateGraphPath, graphElement, graphRead, viewOf } from "./graph";
import { factoryNameProblem, labelRefusal, labelsVersionToSend, NAME_MAX, recordLabelsVersion, refetchLabelledViews, staleWriteReason } from "./rename";

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
  graph: -1,
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
  detect.graph = -1;
  if (detect.world !== state.world) detect.named = [];
  if (again) startDetect(false);
}

function detectPath(): `/api/factories/candidates?${string}` {
  const fedOnly = detect.showAll ? false : settingOn("fedOnly");
  const least = detect.showAll ? 1 : Math.max(1, settingNumber("minMachines"));
  return ("/api/factories/candidates?style=" + encodeURIComponent(settingChoice("naming")) + "&fed_only=" + fedOnly + "&min_machines=" + least) as `/api/factories/candidates?${string}`;
}

function settingsKey(): string {
  return settingChoice("naming") + "|" + settingOn("fedOnly") + "|" + settingNumber("minMachines") + "|" + detect.showAll;
}

function startDetect(keepEdits: boolean): void {
  const ticket = latest("detect");
  const world = state.world;
  detect.busy = true;
  detect.failure = null;
  detect.settingsKey = settingsKey();
  detect.epoch = state.epoch;
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

function rowOf(index: number): CandidateRow | undefined {
  return detect.data ? detect.data.candidates.filter((c) => c.index === index)[0] : undefined;
}

function chosenName(row: CandidateRow): string {
  const edited = detect.edits[row.index];
  return (edited === undefined ? row.suggested_name : edited).trim();
}

function nameCandidate(row: CandidateRow): void {
  const data = detect.data;
  if (!data || detect.saving >= 0) return;
  const name = chosenName(row);
  const problem = factoryNameProblem(name);
  if (problem) {
    detect.nameErrors[row.index] = problem;
    requestRender();
    return;
  }
  delete detect.nameErrors[row.index];
  detect.saving = row.index;
  requestRender();
  send<NamedResponse>("POST", "/api/labels", { name, proposal: row.index, as_of: data.token, version: labelsVersionToSend(data.version) })
    .then(function (reply) {
      recordLabelsVersion(reply.version);
      if (detect.data) {
        detect.data.version = reply.version;
        detect.data.candidates = detect.data.candidates.filter((c) => c.index !== row.index);
      }
      if (detect.graph === row.index) detect.graph = -1;
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
      detect.named = detect.named.filter((n) => n !== reply);
      notify("forgot “" + reply.name + "”");
    })
    .catch(function (failure) {
      if (labelRefusal(failure) === "stale") fail(staleWriteReason("stale") + ", so “" + reply.name + "” was kept; they are reloaded now, undo again");
      else fail("forgetting “" + reply.name + "”: " + friendlyError(failure));
    })
    .then(function () {
      refetchLabelledViews();
      runDetect(true);
    });
}

function flowText(list: Flow[]): string {
  return list.map((f) => perMin(f.per_min, false) + " " + f.name).join(" · ");
}

function makes(row: CandidateRow): Safe {
  const main = row.products.length ? flowText(row.products) : row.buildings.map((b) => b.count + " " + b.name).join(" · ") || "–";
  const more: string[] = [];
  if (row.intermediates.length) more.push("via " + flowText(row.intermediates));
  if (row.sunk.length) more.push("sunk " + flowText(row.sunk));
  if (row.unrouted.length) more.push("goes nowhere " + flowText(row.unrouted));
  if (row.inputs.length) more.push("in " + flowText(row.inputs));
  return html`<div class="cmakes" title="${more.join("\n")}"><span>${main}</span>${more.length ? html`<span class="ictx">${more[0]}</span>` : ""}</div>`;
}

function candidateEntry(row: CandidateRow): Safe {
  const edited = detect.edits[row.index];
  const value = edited === undefined ? row.suggested_name : edited;
  const problem = detect.nameErrors[row.index];
  const skipped = !!detect.skipped[row.index];
  const ctx = html`${!row.confident && chip("guess", "muted", "no clear end product, so this name is a guess")} ${SOURCE[row.fed] || row.fed}${row.region ? " · " + row.region : ""}`;
  const input = html`<input type="text" class="${row.confident ? "" : "guess"}" data-candidate="${row.index}" value="${value}" maxlength="${NAME_MAX}" spellcheck="false" aria-label="${"name for the unnamed cluster of " + counted(row.machines, "machine")}"${problem ? html` aria-invalid="true" title="${problem}"` : ""}>`;
  const id = html`<div class="id">${input}<div class="ctx">${problem ? html`<span class="bad-t">${problem}</span>` : ctx}</div></div>`;
  const map = row.bbox_m ? btn("◎", { extra: "icon", title: "show it on the map, ringed", data: { cact: "map", ci: String(row.index) } }) : "";
  const name = busyBtn(detect.saving === row.index ? "naming…" : "name", { title: "save this name as a factory", disabled: detect.saving >= 0, data: { cact: "name", ci: String(row.index) } });
  const more = html`<span data-ci="${row.index}">${moreBtn("cluster of " + row.machines, ["graph", skipped ? "unskip" : "skip"])}</span>`;
  return entry({ extra: skipped ? "skipped" : "" }, html`${id}${makes(row)}<div class="metric"><b>${count(row.machines)}</b><span>machines</span></div>${actSlots(name, { pin: map, more })}`);
}

function filterLine(data: CandidatesResponse, skipped: number): Safe {
  const parts: string[] = [];
  if (data.hidden.not_fed) parts.push(count(data.hidden.not_fed) + " not fed");
  if (data.hidden.small) parts.push(count(data.hidden.small) + " below " + counted(data.min_machines, "machine"));
  const hidden = data.hidden.not_fed + data.hidden.small;
  const filters = html`<a href="${hashFor("settings")}">filters</a>`;
  const said = detect.showAll ? html`every ${WORDS.unnamedCluster}, ${filters} off` : hidden ? html`${count(hidden)} hidden by the ${filters}: ${parts.join(", ")}` : "";
  const toggle = detect.showAll ? btn("use filters", { data: { cact: "filters" } }) : hidden ? btn("show all", { data: { cact: "all" } }) : "";
  const skip = skipped ? btn(detect.showSkipped ? "hide skipped" : "show " + count(skipped) + " skipped", { data: { cact: "skipped" } }) : "";
  return html`<p class="note cline">${detect.data ? "largest first" : ""}${said ? html` · ${said}` : ""} ${toggle}${skip}</p>`;
}

function namedLines(): Safe {
  return html`${detect.named.map((reply, i) => html`<p class="note cline">named “${reply.name}”, ${counted(reply.machines, "machine")} ${btn("undo", { data: { cact: "undo", ni: String(i) } })}</p>`)}`;
}

/** The block's markup; the graph, when one is open, goes in `.cgraph` (see clusterGraph). */
export function clustersBlock(): Safe {
  const data = detect.data;
  const open = data ? data.candidates.filter((c) => !detect.skipped[c.index]) : [];
  const rows = data ? (detect.showSkipped ? data.candidates : open) : [];
  const label = detect.busy ? "detecting…" : data ? "detect again" : "detect";
  const head = toolrow(dashH(WORDS.unnamedClusters, data ? open.length : undefined), html`<span data-cact="detect">${busyBtn(label, { title: "find the machines no factory covers", disabled: detect.busy })}</span>`);
  let body: Safe;
  if (detect.failure) body = emptyLine(WORDS.unnamedClusters + " could not be read", friendlyError(detect.failure));
  else if (!data) body = note("finds the machines no factory covers yet and suggests a name for each");
  else if (!rows.length) body = emptyLine(data.candidates.length ? "every " + WORDS.unnamedCluster + " here is skipped" : "no " + WORDS.unnamedCluster + " left to show");
  else body = list("cand", "stacked", rows.map(candidateEntry));
  const graph = detect.graph >= 0 ? html`<div class="cgraph"></div>` : "";
  return html`<section class="clusters"${detect.busy ? html` aria-busy="true"` : ""}>${head}${namedLines()}${body}${data ? filterLine(data, data.candidates.length - open.length) : ""}${graph}</section>`;
}

/** Puts the open cluster graph into its slot after the markup is mounted. */
export function clusterGraph(root: HTMLElement): void {
  const slot = root.querySelector<HTMLElement>(".cgraph");
  const row = rowOf(detect.graph);
  if (!slot || !row || !detect.data) return;
  const path = candidateGraphPath(row.index, detect.data.token);
  const read = graphRead(path);
  const title = html`${dashH("graph · " + chosenName(row))}`;
  const wait = pending(read, "the graph", path);
  slot.innerHTML = toolrow(title, btn("close", { data: { cact: "close" } })).safeHtml + (wait ? wait.safeHtml : "");
  if (read.data) slot.appendChild(graphElement("candidate:" + row.index, viewOf(read.data), function () {}));
}

function act(what: string, el: HTMLElement): void {
  const index = Number(el.closest<HTMLElement>("[data-ci]")?.dataset.ci ?? el.dataset.ci);
  const row = rowOf(index);
  if (what === "detect") runDetect();
  else if (what === "filters" || what === "all") {
    detect.showAll = what === "all";
    runDetect(true);
  } else if (what === "skipped") {
    detect.showSkipped = !detect.showSkipped;
    requestRender();
  } else if (what === "undo") {
    const reply = detect.named[Number(el.dataset.ni)];
    if (reply) forgetNamed(reply);
  } else if (what === "close") {
    detect.graph = -1;
    requestRender();
  } else if (what === "name" && row) nameCandidate(row);
  else if (what === "map" && row && row.bbox_m && detect.data) {
    const box = row.bbox_m;
    const token = detect.data.token;
    leaveDashThen(function () {
      showBox(box);
      ringCandidate(row.selector, token, chosenName(row));
    });
  }
}

export function wireClusters(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const more = target.closest<HTMLElement>(".clusters .more[data-menu]");
    if (more) {
      const index = Number(more.closest<HTMLElement>("[data-ci]")?.dataset.ci);
      const row = rowOf(index);
      if (!row) return;
      const skipped = !!detect.skipped[index];
      const showGraph = function () {
        detect.graph = index;
        requestRender();
      };
      const toggleSkip = function () {
        if (skipped) delete detect.skipped[index];
        else detect.skipped[index] = true;
        requestRender();
      };
      openMenu(more, [
        { label: "graph", act: showGraph },
        { label: skipped ? "unskip" : "skip", act: toggleSkip },
      ]);
      return;
    }
    const hit = target.closest<HTMLElement>("[data-cact]");
    if (hit && root.contains(hit) && !(hit.querySelector("button") as HTMLButtonElement | null)?.disabled) act(hit.dataset.cact || "", hit);
  });
  root.addEventListener("input", function (event) {
    const input = event.target as HTMLInputElement;
    if (!input.dataset || input.dataset.candidate === undefined) return;
    const index = Number(input.dataset.candidate);
    detect.edits[index] = input.value;
    if (detect.nameErrors[index]) {
      delete detect.nameErrors[index];
      input.removeAttribute("aria-invalid");
    }
  });
  root.addEventListener("keydown", function (event) {
    const input = event.target as HTMLInputElement;
    if (!input.dataset || input.dataset.candidate === undefined) return;
    const row = rowOf(Number(input.dataset.candidate));
    if (!row) return;
    if (event.key === "Enter") nameCandidate(row);
    else if (event.key === "Escape") {
      delete detect.edits[row.index];
      delete detect.nameErrors[row.index];
      input.value = row.suggested_name;
    }
  });
}

function settingChanged(): void {
  if ((detect.data || detect.busy) && settingsKey() !== detect.settingsKey) runDetect(true);
  else requestRender();
}

export function wireDetect(): void {
  detect.settingsKey = settingsKey();
  onSetting(settingChanged);
}
