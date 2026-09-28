/* The track tab: where a half-built plan stands against the save, wave by wave and job by job.
 * See docs/planner-p4_contract.md §2 F2–F5 and §8. */

import { askButton, askMarks } from "./asks";
import { renderAsks } from "./asks-card";
import { copyText } from "./copy";
import { button, chip, empty, error, fieldError, idChip, loading, pressed, table } from "./dashkit";
import { make } from "./dom";
import { count, mw, num, range, signed } from "./format";
import { onMap } from "./nav";
import { showBox, showPoint, vitals } from "./panel";
import { bench, changed, gesture, loadFeeders, loadTrack, pickStage } from "./planner-core";
import { recipesButton } from "./planner-result";
import { headroom } from "./powerview";
import { actionTone, tone } from "./states";
import { fail, note } from "./toast";
import { counted, VERB, W } from "./words";

import type { Column } from "./dashkit";
import type { AskAbout, TrackCost, TrackResponse, TrackRow, TrackSiteRow, TrackStage, TrackState, Feeder } from "./api-shapes";
import type { Selection } from "./planner-core";

type Box = [number, number, number, number];

var HEADROOM_MAX = 1000000;
var NARROW = window.matchMedia("(max-width: 899px)");
var STARTUP_CTL = "track-startup";

var headroomProblem = { key: "", text: "", raw: "" };

NARROW.addEventListener("change", function () {
  if (bench.tab === "track") changed();
});

function box(b: number[] | null): Box | null {
  return b && b.length === 4 ? (b as Box) : null;
}

function mapButton(bbox: number[] | null, what: string, nodes: boolean): HTMLButtonElement | null {
  var at = box(bbox);
  if (!at) return null;
  var target = at;
  return button(
    "map",
    function () {
      onMap(function () {
        showBox(target, { layers: nodes ? ["machines", "nodes"] : ["machines"] });
      });
    },
    { map: true, title: "fly the map to " + what + " and outline it", label: "show " + what + " on the map" }
  );
}

function copyIds(row: TrackRow): HTMLButtonElement | null {
  var ids = row.selectors
    ? row.selectors.split(",").filter(function (s) {
        return s.trim() !== "";
      })
    : [];
  if (!ids.length) return null;
  return button(
    "copy ids",
    function () {
      copyText(ids.join(",")).then(
        function () {
          note("copied " + counted(ids.length, "id"));
        },
        function () {
          fail("could not copy the ids: the browser refused");
        }
      );
    },
    { title: "copy the " + counted(ids.length, "selector") + " of this job for a tool call", label: "copy ids of " + row.process }
  );
}

function about(kind: string, label: string, ref: string): AskAbout {
  var plan = bench.plan!;
  return { kind: kind, label: label, ref: ref, plan: bench.key, rev: plan.rev };
}

function marks(cell: HTMLElement, kind: string, ref: string): void {
  askMarks(bench.key, kind, ref).forEach(function (a) {
    cell.appendChild(idChip(a.id, a.text));
  });
}

function stateChips(parent: HTMLElement, states: TrackState[]): void {
  states.forEach(function (s) {
    if (!s.count) return;
    var t = tone(s.state);
    if (t === "ok") return;
    parent.appendChild(chip(count(s.count) + " " + s.state, t));
  });
}

function verb(row: TrackRow): string {
  var build = VERB.build + " " + range(row.build, row.build_max);
  if (row.verb === "unpause") return VERB.unpause + " " + count(row.count) + (row.build > 0 ? ", then " + build : "");
  if (row.verb === "setrecipe") return VERB.setrecipe + " " + count(row.count) + (row.build > 0 ? ", then " + build : "");
  if (row.verb === "build") return build;
  return VERB.ok || "–";
}

function stagesWord(stages: number[]): string {
  if (!stages.length) return "–";
  return (stages.length === 1 ? "stage " : "stages ") + stages.join(", ");
}

function nextLine(d: TrackResponse): string {
  var parts: string[] = [];
  if (d.unpause) parts.push(VERB.unpause + " " + count(d.unpause));
  if (d.setrecipe) parts.push(VERB.setrecipe + " " + count(d.setrecipe));
  if (d.to_build || d.to_build_max) parts.push(VERB.build + " " + range(d.to_build, d.to_build_max));
  return parts.length ? parts.join(" · ") : "nothing to do: every job of the plan stands";
}

function headline(parent: HTMLElement, d: TrackResponse, asked: number): void {
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", W.track + " · v" + d.rev));
  if (asked) title.appendChild(make("span", "plan-status", "tracking v" + asked + "…"));
  card.appendChild(title);
  var text = d.scope_error ? "" : d.stage_text || (d.count ? "" : "no startup order fits " + mw(d.startup.headroom_mw) + " of headroom");
  if (text) card.appendChild(make("p", "plan-headline", text));
  var facts = ["save " + d.age_note];
  if (d.scope_note) facts.push(d.scope_note);
  if (d.drift_note) facts.push(d.drift_note);
  card.appendChild(make("p", "dash-note", facts.join(" · ")));
  d.notes.forEach(function (n) {
    card.appendChild(make("p", "dash-note", n));
  });
  parent.appendChild(card);
}

function floorTen(value: number): number {
  return Math.floor(value / 10) * 10;
}

function setHeadroom(value: number | null): void {
  var plan = bench.plan;
  if (!plan || (plan.headroom_mw === undefined ? null : plan.headroom_mw) === value) return;
  gesture([{ op: "set", field: "headroom_mw", value: value }]);
}

function givenField(parent: HTMLElement, stored: number | null, measured: number): void {
  var field = make("input", "dash-number");
  field.type = "number";
  field.step = "any";
  field.min = "0";
  var shown = stored !== null && stored !== measured ? String(stored) : "";
  var key = bench.key;
  var kept = headroomProblem.key === key && headroomProblem.text ? headroomProblem.raw : shown;
  field.value = kept;
  field.defaultValue = shown;
  field.setAttribute("data-ctl", "track-headroom");
  field.setAttribute("aria-label", "given startup headroom in MW");
  var commit = function () {
    var raw = field.value.trim();
    if (raw === field.defaultValue && !headroomProblem.text) return;
    var n = Number(raw);
    if (raw === "" || !isFinite(n) || !(n > 0) || n > HEADROOM_MAX) {
      headroomProblem = { key: key, text: "a headroom is more than 0 and at most " + count(HEADROOM_MAX) + " MW", raw: field.value };
      fieldError(field, headroomProblem.text);
      return;
    }
    headroomProblem = { key: "", text: "", raw: "" };
    fieldError(field, "");
    field.defaultValue = field.value;
    setHeadroom(n);
  };
  field.onchange = commit;
  field.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      field.value = field.defaultValue;
      headroomProblem = { key: "", text: "", raw: "" };
      fieldError(field, "");
      field.blur();
    }
  };
  parent.appendChild(make("span", "plan-sub", "given (MW)"));
  parent.appendChild(field);
  if (headroomProblem.key === key && headroomProblem.text) fieldError(field, headroomProblem.text);
}

function factoryNames(current: string): string[] {
  var health = vitals().health;
  var names = health
    ? health.factories.map(function (f) {
        return f.name;
      })
    : [];
  if (current && names.indexOf(current) < 0) names.push(current);
  return names.sort(function (a, b) {
    return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
  });
}

function controls(parent: HTMLElement, d: TrackResponse): void {
  var plan = bench.plan!;
  var stored = plan.headroom_mw === undefined ? d.headroom_mw : plan.headroom_mw;
  var card = make("section", "dash-card plan-bench track-controls");
  var row = make("div", "plan-row");
  row.appendChild(make("span", "plan-label", W.startupHeadroom));
  var body = make("div", "plan-controls");
  var measured = floorTen(d.power.measured_headroom_mw);
  body.appendChild(
    pressed(
      "nameplate " + mw(d.power.headroom_mw),
      stored === null,
      function () {
        setHeadroom(null);
      },
      { title: "use the save's nameplate headroom: generation minus nameplate draw", disabled: bench.gone }
    )
  );
  body.appendChild(
    pressed(
      "measured " + mw(d.power.measured_headroom_mw),
      stored !== null && stored === measured,
      function () {
        setHeadroom(measured);
      },
      {
        title: measured > 0 ? "store the measured headroom, rounded down to " + mw(measured) : "the measured headroom is not above 0 MW in this save",
        disabled: bench.gone || measured <= 0,
      }
    )
  );
  givenField(body, stored, measured);
  row.appendChild(body);
  card.appendChild(row);
  card.appendChild(make("p", "dash-note", "startup order uses " + mw(d.startup.headroom_mw) + ", " + d.startup.headroom_source));
  var scope = make("div", "plan-row");
  scope.appendChild(make("span", "plan-label", W.countAsBuilt));
  var pickBox = make("div", "plan-controls plan-stack");
  if (d.scope_error) {
    var bad = make("p", "plan-invalid", "“" + d.scope + "” has no machines in this save");
    bad.setAttribute("role", "alert");
    pickBox.appendChild(bad);
  }
  var pick = make("select", "dash-select");
  pick.setAttribute("data-ctl", "track-scope");
  pick.setAttribute("aria-label", W.countAsBuilt);
  pick.disabled = bench.gone;
  var whole = make("option", "", W.wholeWorld);
  whole.value = "";
  pick.appendChild(whole);
  factoryNames(plan.factory).forEach(function (name) {
    var option = make("option", "", name);
    option.value = name;
    pick.appendChild(option);
  });
  pick.value = plan.factory;
  pick.onchange = function () {
    if (pick.value !== plan.factory) gesture([{ op: "set", field: "factory", value: pick.value }]);
  };
  pickBox.appendChild(pick);
  scope.appendChild(pickBox);
  card.appendChild(scope);
  parent.appendChild(card);
}

function notice(parent: HTMLElement): void {
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

function stateCell(s: TrackStage): HTMLElement {
  var cell = make("span", "track-state", s.state);
  var t = actionTone(s.states);
  if (t) cell.appendChild(chip(W.needAction, t));
  stateChips(cell, s.states);
  return cell;
}

function stageActions(s: TrackStage, total: number): HTMLElement {
  var acts = make("span", "dash-acts");
  var label = W.stage(s.index, total);
  var there = mapButton(s.bbox_m, label, false);
  if (there) acts.appendChild(there);
  if (!bench.gone) acts.appendChild(askButton(about("stage", label, String(s.index)), "stage:" + s.index));
  return acts;
}

function stageLead(s: TrackStage): HTMLElement {
  var cell = make("span", "", "stage " + s.index);
  marks(cell, "stage", String(s.index));
  return cell;
}

function stages(parent: HTMLElement, d: TrackResponse): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", d.count ? "stages · " + counted(d.count, "stage") : "stages"));
  notice(card);
  if (!d.stages.length) {
    empty(card, "no startup order fits " + mw(d.startup.headroom_mw) + " of headroom", "pick the measured headroom or give one above");
    parent.appendChild(card);
    return;
  }
  var picked = bench.track.stage;
  var lead: Column<TrackStage> = { key: "stage", label: "stage", render: stageLead };
  var built: Column<TrackStage> = {
    key: "built",
    label: "built",
    align: "right",
    title: "machines of this wave standing in the save; a range where the save cannot tell them apart",
    render: function (s) {
      return range(s.built, s.built_max);
    },
  };
  var state: Column<TrackStage> = { key: "state", label: "state", render: stateCell };
  var rest: Column<TrackStage>[] = [
    {
      key: "on",
      label: "on",
      align: "right",
      title: "machines this wave switches on",
      render: function (s) {
        return count(s.machines);
      },
    },
    {
      key: "running",
      label: "running",
      align: "right",
      title: "machines of this wave a productivity monitor proves running; – where none is monitored",
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
      title: "headroom left once this wave runs",
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
  var columns = NARROW.matches ? [lead, built, state].concat(rest, [acts]) : [lead, rest[0]!, built].concat(rest.slice(1), [state, acts]);
  var frame = table(columns, d.stages, {
    onRow: function (s) {
      pickStage(s.index);
    },
    rowClass: function (s) {
      return s.index === picked ? "track-stage picked" : "track-stage";
    },
    caption: "startup stages",
  });
  frame.querySelectorAll("tbody tr").forEach(function (tr, i) {
    var s = d.stages[i];
    if (!s) return;
    tr.setAttribute("aria-selected", String(s.index === picked));
    tr.setAttribute("aria-label", W.stage(s.index, d.count) + ": " + s.state);
  });
  card.appendChild(frame);
  parent.appendChild(card);
}

function jobActions(row: TrackRow): HTMLElement {
  var acts = make("span", "dash-acts");
  var there = mapButton(row.bbox_m, row.process, row.targets.length > 0);
  if (there) acts.appendChild(there);
  var ids = copyIds(row);
  if (ids) acts.appendChild(ids);
  if (!bench.gone) {
    if (row.kind === "recipe" && row.item && row.recipe_id) acts.appendChild(recipesButton(row.item, row.process, "track"));
    acts.appendChild(askButton(about("process", row.process, row.id), "job:" + row.id));
  }
  return acts;
}

function processCell(row: TrackRow): HTMLElement {
  var cell = make("span", "plan-recipe", row.process);
  if (row.new_building) cell.appendChild(chip("new building", "muted", "this building is not unlocked or not yet on the ground anywhere"));
  marks(cell, "process", row.id);
  return cell;
}

function stageFilter(parent: HTMLElement, n: number, total: number): void {
  var line = make("div", "plan-line");
  var c = make("span", "plan-chip", "only " + W.stage(n, total));
  var x = make("button", "plan-chip-x", "×");
  x.type = "button";
  x.title = "show the jobs of every stage";
  x.setAttribute("aria-label", "show the jobs of every stage");
  x.onclick = function () {
    pickStage(n);
  };
  c.appendChild(x);
  line.appendChild(c);
  parent.appendChild(line);
}

function jobs(parent: HTMLElement, d: TrackResponse, select: (s: Selection) => void): void {
  var view = bench.track;
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "jobs · " + counted(d.rows.length, "job")));
  var next = make("p", "plan-facts");
  next.appendChild(make("b", "", "next: "));
  next.appendChild(document.createTextNode(nextLine(d)));
  card.appendChild(next);
  var n = view.stage;
  if (n) stageFilter(card, n, d.count);
  var rows = n
    ? d.rows.filter(function (r) {
        return r.stages.indexOf(n) >= 0;
      })
    : d.rows;
  if (!rows.length) {
    empty(card, n ? "no jobs in stage " + n : "the plan has no jobs");
  } else {
    var lead: Column<TrackRow> = { key: "process", label: "process", render: processCell };
    var built: Column<TrackRow> = {
      key: "built",
      label: "built",
      align: "right",
      title: "matching machines in the save; a range where the save cannot tell them apart",
      render: function (r) {
        return r.have_min === null ? count(r.have) : range(r.have_min, r.have);
      },
    };
    var action: Column<TrackRow> = { key: "action", label: "action", className: "dash-nowrap", render: verb };
    var building: Column<TrackRow> = {
      key: "building",
      label: "building",
      render: function (r) {
        return r.building;
      },
    };
    var need: Column<TrackRow> = {
      key: "need",
      label: "need",
      align: "right",
      render: function (r) {
        return count(r.need);
      },
    };
    var running: Column<TrackRow> = {
      key: "running",
      label: "running",
      align: "right",
      title: "matched machines a productivity monitor proves running; – where none is monitored",
      render: function (r) {
        return r.running === null ? "–" : count(r.running);
      },
    };
    var where: Column<TrackRow> = {
      key: "where",
      label: "where",
      className: "dash-nowrap",
      title: "the startup stages this job is switched on in",
      render: function (r) {
        return stagesWord(r.stages);
      },
    };
    var noteCol: Column<TrackRow> = {
      key: "note",
      label: "note",
      className: "dash-sub track-note",
      render: function (r) {
        return r.note || "";
      },
    };
    var acts: Column<TrackRow> = { key: "acts", label: "", render: jobActions };
    var columns = NARROW.matches
      ? [lead, built, action, building, need, running, where, noteCol, acts]
      : [lead, building, need, built, running, action, where, noteCol, acts];
    card.appendChild(
      table(columns, rows, {
        onRow: function (r) {
          bench.picked = r.id;
          select({ kind: "process", label: r.process, ref: r.id });
        },
        rowClass: function (r) {
          return bench.picked === r.id ? "plan-picked" : "";
        },
        caption: "jobs",
      })
    );
  }
  if (d.neighbours.length) {
    card.appendChild(
      make(
        "p",
        "dash-note",
        "nearby, not in the plan: " +
          d.neighbours
            .map(function (nb) {
              return nb.label + " ×" + count(nb.count);
            })
            .join(", ")
      )
    );
  }
  parent.appendChild(card);
}

function short(parent: HTMLElement, cost: TrackCost[]): void {
  if (!cost.length) return;
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "short · " + counted(cost.length, "item")));
  var columns: Column<TrackCost>[] = [
    {
      key: "item",
      label: "item",
      render: function (c) {
        var cell = make("span", "", c.name);
        marks(cell, "item", c.item);
        return cell;
      },
    },
    {
      key: "need",
      label: "need",
      align: "right",
      render: function (c) {
        return num(c.need, 0);
      },
    },
    {
      key: "stock",
      label: "stock",
      align: "right",
      render: function (c) {
        return num(c.stock, 0);
      },
    },
    {
      key: "lines",
      label: "lines",
      align: "right",
      title: "machines in the save whose recipe makes this item",
      render: function (c) {
        return count(c.lines);
      },
    },
  ];
  if (!bench.gone) {
    columns.push({
      key: "acts",
      label: "",
      render: function (c) {
        return askButton(about("item", c.name, c.item), "item:" + c.item);
      },
    });
  }
  card.appendChild(table(columns, cost, { caption: "short items" }));
  parent.appendChild(card);
}

function site(parent: HTMLElement, d: TrackResponse): void {
  var s = d.site;
  if (!s) return;
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "on site"));
  if (s.text) card.appendChild(make("p", "dash-note", s.text));
  var columns: Column<TrackSiteRow>[] = [
    {
      key: "name",
      label: "building",
      render: function (r) {
        return r.name;
      },
    },
    {
      key: "planned",
      label: "planned",
      align: "right",
      render: function (r) {
        return count(r.planned);
      },
    },
    {
      key: "standing",
      label: "on site",
      align: "right",
      render: function (r) {
        return count(r.standing);
      },
    },
    {
      key: "delta",
      label: "Δ",
      align: "right",
      title: "on site minus planned",
      render: function (r) {
        return signed(r.standing - r.planned, count);
      },
    },
  ];
  card.appendChild(table(columns, s.rows, { caption: "on site" }));
  parent.appendChild(card);
}

function feederActions(r: Feeder): HTMLElement {
  var acts = make("span", "dash-acts");
  var x = r.x_m;
  var y = r.y_m;
  if (x !== null && y !== null) {
    acts.appendChild(
      button(
        "map",
        function () {
          onMap(function () {
            showPoint(x as number, y as number, { label: r.name, layers: ["machines"] });
          });
        },
        { map: true, title: "fly the map to this " + r.name, label: "show this " + r.name + " on the map" }
      )
    );
  }
  acts.appendChild(
    button(
      "copy id",
      function () {
        copyText("machine:" + r.instance).then(
          function () {
            note("copied the id of this " + r.name);
          },
          function () {
            fail("could not copy the id: the browser refused");
          }
        );
      },
      { title: "copy this extractor's selector for a tool call", label: "copy the id of this " + r.name }
    )
  );
  return acts;
}

function feeders(card: HTMLElement): void {
  var f = bench.track.feeders;
  if (!f) {
    card.appendChild(
      button("what the waves stand on", loadFeeders, { title: "list the extractors that feed running generators today (takes about a second)" })
    );
    return;
  }
  var sub = make("div", "track-feeders");
  sub.appendChild(make("h3", "dash-h", "what the waves stand on"));
  if (f.busy) loading(sub, "what the waves stand on");
  else if (f.error) error(sub, "what the waves stand on", f.error, loadFeeders);
  else if (f.data && !f.data.feeders.length) empty(sub, "no extractor feeds a running generator");
  else if (f.data) {
    var columns: Column<Feeder>[] = [
      {
        key: "name",
        label: "extractor",
        render: function (r) {
          return r.name;
        },
      },
      {
        key: "mw",
        label: "generation it feeds",
        align: "right",
        render: function (r) {
          return mw(r.mw);
        },
      },
      { key: "acts", label: "", render: feederActions },
    ];
    sub.appendChild(table(columns, f.data.feeders, { caption: "extractors feeding running generators" }));
    if (f.data.text) sub.appendChild(make("p", "dash-note", f.data.text));
  }
  card.appendChild(sub);
}

function startup(parent: HTMLElement, d: TrackResponse): void {
  var s = d.startup;
  var card = make("section", "dash-card");
  var h = make("h2", "dash-h", "startup order");
  h.tabIndex = -1;
  h.setAttribute("data-ctl", STARTUP_CTL);
  card.appendChild(h);
  card.appendChild(make("p", "plan-facts", "headroom " + mw(s.headroom_mw) + ", " + s.headroom_source));
  card.appendChild(
    make(
      "p",
      "plan-facts",
      "plant draw " + mw(s.plant_draw_mw) + " · generation " + mw(s.plant_generation_mw) + " · minimum slice " + mw(s.minimum_slice_mw)
    )
  );
  s.warnings.forEach(function (w) {
    card.appendChild(make("p", "plan-warning", w));
  });
  d.stages.forEach(function (st) {
    if (!st.waits_for_fill) return;
    card.appendChild(make("p", "dash-note", "stage " + st.index + ": ≥ " + num(st.fill_s, 0) + " s before its generators produce"));
  });
  feeders(card);
  parent.appendChild(card);
}

function caveats(parent: HTMLElement, d: TrackResponse): void {
  if (!d.caveats.length) return;
  var foot = make("div", "track-caveats");
  d.caveats.forEach(function (c) {
    foot.appendChild(make("p", "dash-note", c));
  });
  parent.appendChild(foot);
}

export function focusStartup(root: HTMLElement): boolean {
  var h = root.querySelector<HTMLElement>('[data-ctl="' + STARTUP_CTL + '"]');
  if (!h) return false;
  h.focus();
  h.scrollIntoView({ block: "start" });
  return true;
}

export function renderTrack(parent: HTMLElement, select: (s: Selection) => void): void {
  var view = bench.track;
  var d = view.data;
  var frame = make("div", "plan-track" + (view.asked && d ? " plan-stale" : ""));
  parent.appendChild(frame);
  if (view.error) error(frame, "the track", view.error, loadTrack);
  if (!d) {
    if (!view.error) loading(frame, "the track");
    return;
  }
  headline(frame, d, view.asked);
  if (!d.feasible) {
    frame.appendChild(make("p", "plan-headline bad", "not solvable: " + (d.cause || d.headline)));
    return;
  }
  if (d.empty) {
    empty(frame, "the plan builds nothing: nothing to track");
    return;
  }
  controls(frame, d);
  if (d.scope_error) {
    renderAsks(frame, changed, bench.key);
    return;
  }
  stages(frame, d);
  jobs(frame, d, select);
  short(frame, d.cost);
  site(frame, d);
  startup(frame, d);
  caveats(frame, d);
  renderAsks(frame, changed, bench.key);
}
