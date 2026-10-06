/* The track tab: where a half-built plan stands against the save, stage by stage and job by job.
 * See docs/planner-p4_contract.md §2 F2–F5 and §8. */

import { askButton } from "../../chat/asks";
import { renderAsks } from "../../chat/asks-card";
import { button, chip, empty, error, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, countRange, mw } from "../../kit/format";
import { boxMapButton, builtColumn, builtLine } from "./planner-built";
import { loadTrack, pickStage } from "./planner-reads";
import { bench, changed } from "./planner-state";
import { headroomControls } from "./planner-track-headroom";
import { startupCard, STARTUP_CTL } from "./planner-track-startup";
import { appendAskMarks, askAbout, jobsCard, onSiteCard, shortItemsCard, stageCtl, stateChips, takeRefocusCtl, TRACK_TABLE_NARROW } from "./planner-track-tables";
import { headroom } from "../power-ledger";
import { actionTone } from "../machine-states";
import { counted, WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { FocusSelection, TrackResponse, TrackStage } from "../../api/shapes";

function trackHeadline(parent: HTMLElement, d: TrackResponse, asked: number): void {
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", WORDS.track + " · v" + d.rev));
  if (asked) title.appendChild(make("span", "plan-status", "tracking v" + asked + "…"));
  card.appendChild(title);
  var text = d.scope_error ? "" : d.stage_text || (d.count ? "" : "no startup order fits " + mw(d.startup.headroom_mw) + " of headroom");
  if (text) card.appendChild(make("p", "plan-headline", text));
  builtLine(card, d.built_at);
  var facts = [d.written_ago ? "save written " + d.written_ago : "as of the save shown in the header"];
  if (d.scope_note) facts.push(d.scope_note);
  if (d.drift_note) facts.push(d.drift_note);
  card.appendChild(make("p", "dash-note", facts.join(" · "))).title = d.age_note;
  d.notes.forEach(function (note) {
    card.appendChild(make("p", "dash-note", note));
  });
  parent.appendChild(card);
}

function renumberNotice(parent: HTMLElement): void {
  var view = bench.track;
  if (!view.notice) return;
  var line = make("div", "plan-warning track-notice");
  line.setAttribute("role", "status");
  line.appendChild(make("span", "", view.notice));
  line.appendChild(
    button(
      "ok",
      function () {
        view.notice = "";
        changed();
      },
      { title: "dismiss this notice", label: "dismiss the stage renumbering notice" }
    )
  );
  parent.appendChild(line);
}

function stateCell(stage: TrackStage): HTMLElement {
  var cell = make("span", "track-state", stage.state);
  var actionNeeded = actionTone(stage.states);
  if (actionNeeded) cell.appendChild(chip(WORDS.needAction, actionNeeded));
  stateChips(cell, stage.states);
  return cell;
}

function stageActions(stage: TrackStage, total: number): HTMLElement {
  var acts = make("span", "dash-acts");
  var label = WORDS.stage(stage.index, total);
  var there = boxMapButton(stage.bbox_m, label, []);
  if (there) acts.appendChild(there);
  if (!bench.gone) acts.appendChild(askButton(askAbout("stage", label, String(stage.index)), "stage:" + stage.index));
  return acts;
}

function stageLead(stage: TrackStage): HTMLElement {
  var cell = make("span", "", WORDS.stageUnit + " " + stage.index);
  appendAskMarks(cell, "stage", String(stage.index));
  return cell;
}

function stageColumns(d: TrackResponse): Column<TrackStage>[] {
  var lead: Column<TrackStage> = { key: "stage", label: "stage", render: stageLead };
  var built: Column<TrackStage> = {
    key: "built",
    label: builtColumn(d.built_at),
    align: "right",
    className: "dash-nowrap",
    title: "machines of this " + WORDS.stageUnit + " standing in the save; a range where the save cannot tell them apart",
    render: function (s) {
      return countRange(s.built, s.built_max);
    },
  };
  var stateColumn: Column<TrackStage> = { key: "state", label: "state", render: stateCell };
  var rest: Column<TrackStage>[] = [
    {
      key: "on",
      label: "on",
      align: "right",
      title: "machines this " + WORDS.stageUnit + " switches on",
      render: function (s) {
        return count(s.machines);
      },
    },
    {
      key: "running",
      label: "running",
      align: "right",
      title: "machines of this " + WORDS.stageUnit + " a productivity monitor proves running; – where none is monitored",
      render: function (s) {
        return s.running === null ? "–" : count(s.running);
      },
    },
    {
      key: "mw",
      label: "MW draw / gen",
      align: "right",
      render: function (s) {
        return mw(s.draw_mw) + " / " + mw(s.generation_mw);
      },
    },
    {
      key: "free",
      label: "free after",
      align: "right",
      title: "headroom left once this " + WORDS.stageUnit + " runs",
      render: function (s) {
        return headroom(s.available_after);
      },
    },
  ];
  var acts: Column<TrackStage> = {
    key: "acts",
    label: "",
    render: function (s) {
      return stageActions(s, d.count);
    },
  };
  return TRACK_TABLE_NARROW.matches ? [lead, built, stateColumn].concat(rest, [acts]) : [lead, rest[0]!, built].concat(rest.slice(1), [stateColumn, acts]);
}

function stagesCard(parent: HTMLElement, d: TrackResponse): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", d.count ? WORDS.stages + " · " + counted(d.count, WORDS.stageUnit) : WORDS.stages));
  renumberNotice(card);
  if (!d.stages.length) {
    empty(card, "no " + WORDS.stages + ": see the headline above", "pick another headroom or give one above");
    parent.appendChild(card);
    return;
  }
  var picked = bench.track.stage;
  var frame = table(stageColumns(d), d.stages, {
    onRow: function (s) {
      pickStage(s.index);
    },
    rowClass: function (s) {
      return s.index === picked ? "track-stage picked" : "track-stage";
    },
    caption: "startup stages",
  });
  frame.querySelectorAll("tbody tr").forEach(function (tr, i) {
    var stage = d.stages[i];
    if (!stage) return;
    tr.setAttribute("data-ctl", stageCtl(stage.index));
    tr.setAttribute("aria-selected", String(stage.index === picked));
    tr.setAttribute("aria-label", WORDS.stage(stage.index, d.count) + ": " + stage.state);
  });
  card.appendChild(frame);
  parent.appendChild(card);
}

export function focusStartup(root: HTMLElement): boolean {
  var heading = root.querySelector<HTMLElement>('[data-ctl="' + STARTUP_CTL + '"]');
  if (!heading) return false;
  heading.focus();
  heading.scrollIntoView({ block: "start" });
  return true;
}

export function settleTrackFocus(root: HTMLElement): void {
  var ctl = takeRefocusCtl();
  if (!ctl) return;
  var row = root.querySelector<HTMLElement>('[data-ctl="' + ctl + '"]');
  if (row) row.focus();
}

export function revealStage(root: HTMLElement, n: number): void {
  var row = root.querySelector<HTMLElement>('[data-ctl="' + stageCtl(n) + '"]');
  if (row) row.scrollIntoView({ block: "center" });
}

export function renderTrack(parent: HTMLElement, select: (selection: FocusSelection) => void): void {
  var view = bench.track;
  var d = view.data;
  var frame = make("div", "plan-track" + (view.asked && d ? " plan-stale" : ""));
  parent.appendChild(frame);
  if (view.error) error(frame, "the track", view.error, loadTrack);
  if (!d) {
    if (!view.error) loading(frame, "the track");
    return;
  }
  trackHeadline(frame, d, view.asked);
  if (!d.feasible) {
    frame.appendChild(make("p", "plan-headline bad", "not solvable: " + (d.cause || d.headline)));
    return;
  }
  if (d.empty) {
    empty(frame, "the plan builds nothing: nothing to track");
    return;
  }
  headroomControls(frame, d);
  if (d.scope_error) {
    renderAsks(frame, changed, bench.key);
    return;
  }
  stagesCard(frame, d);
  jobsCard(frame, d, select);
  shortItemsCard(frame, d.cost);
  onSiteCard(frame, d);
  startupCard(frame, d);
  renderAsks(frame, changed, bench.key);
}
