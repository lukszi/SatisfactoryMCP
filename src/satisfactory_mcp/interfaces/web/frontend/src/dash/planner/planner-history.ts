/* Plan history on the page: the Versions list, a version viewed read-only, and the Activity panel.
 * See docs/plan_management.md. */

import { get } from "../../api/client";
import { button, empty, error, link, loading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { ageShort } from "../../kit/format";
import { go, withQuery } from "../../app/nav";
import { argsWords } from "./planner-bench";
import { actorWord, bench, changed, commitWords, duplicatePlan, inbox, restoreRev, undoIn } from "./planner-core";
import { renderVersionResult } from "./planner-result";
import { state } from "../../app/state";
import { fail, friendlyError, notify } from "../../kit/toast";
import { counted, objectiveText, WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { ActivityResponse, ActivityRow, VersionRow } from "../../api/shapes";
import type { Selection } from "./planner-core";

var ACTIVITY_LIMIT = 50;

var activity = {
  world: "",
  data: null as ActivityResponse | null,
  error: "",
  filter: "all",
};
var activitySeq = 0;

export function planDash(key: string, rev?: number): string {
  return "planner/" + key + (rev ? "/v" + rev : "");
}

function copyTo(rev?: number): void {
  duplicatePlan(rev)
    .then(function (key) {
      notify("copied to a new plan");
      go(planDash(key));
    })
    .catch(function (reason) {
      fail(friendlyError(reason));
    });
}

export function duplicateButton(rev?: number): HTMLButtonElement {
  return button(
    rev ? "duplicate v" + rev : "duplicate",
    function () {
      copyTo(rev);
    },
    { title: "a new plan at v1 with this version's request; this plan is untouched" }
  );
}

function stateWords(row: VersionRow, head: number): string {
  var parts: string[] = [];
  if (row.rev === head) parts.push("head");
  if (row.undone_by) parts.push("undone in v" + row.undone_by);
  if (row.restores) parts.push("restores v" + row.restores);
  if (row.merged_over.length) parts.push("merged over " + row.merged_over.map(function (r) { return "v" + r; }).join(", "));
  return parts.join(" · ");
}

function versionActs(row: VersionRow, head: number): HTMLElement {
  var box = make("span", "dash-acts");
  var key = bench.key;
  box.appendChild(
    button(
      "view",
      function () {
        go(planDash(key, row.rev));
      },
      { title: "open v" + row.rev + " read-only", label: "view v" + row.rev, disabled: row.rev === bench.view }
    )
  );
  if (row.rev !== head && !bench.gone) {
    box.appendChild(
      button(
        "restore",
        function () {
          restoreRev(row.rev);
        },
        { title: "a new version equal to v" + row.rev + "; nothing after it is lost", label: "restore v" + row.rev }
      )
    );
  }
  return box;
}

export function renderVersions(parent: HTMLElement): void {
  if (!bench.versionsOpen) return;
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "versions"));
  var data = bench.versions;
  if (bench.versionsError) error(card, "the versions", bench.versionsError);
  else if (!data || data.key !== bench.key) loading(card, "versions");
  else {
    var head = data.head;
    var columns: Column<VersionRow>[] = [
      { key: "acts", label: "", render: function (r) { return versionActs(r, head); } },
      {
        key: "rev",
        label: "version",
        align: "right",
        render: function (r) {
          return "v" + r.rev;
        },
      },
      {
        key: "who",
        label: "by",
        render: function (r) {
          return actorWord(r.actor);
        },
      },
      {
        key: "when",
        label: "when",
        className: "dash-sub",
        render: function (r) {
          return ageShort(r.ts) + " ago";
        },
      },
      {
        key: "what",
        label: "change",
        render: function (r) {
          return commitWords(r.text);
        },
      },
      {
        key: "state",
        label: "",
        className: "dash-sub",
        render: function (r) {
          return stateWords(r, head);
        },
      },
    ];
    card.appendChild(
      table(columns, data.versions, {
        rowClass: function (r) {
          return r.rev === bench.view ? "plan-picked" : "";
        },
        caption: "versions of this plan",
      })
    );
    card.appendChild(make("p", "dash-note", "restore adds a new version equal to the old one; view opens a version read-only"));
  }
  parent.appendChild(card);
}

export function renderView(parent: HTMLElement, select: (s: Selection) => void): void {
  var rev = bench.view;
  var plan = bench.plan!;
  var card = make("section", "dash-card plan-strip");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "v" + rev + " · read-only"));
  title.appendChild(make("span", "plan-status", "the plan is at v" + plan.rev));
  card.appendChild(title);
  var acts = make("div", "dash-acts");
  acts.appendChild(
    button("back to v" + plan.rev, function () {
      go(planDash(bench.key));
    })
  );
  if (rev !== plan.rev && !bench.gone) {
    acts.appendChild(
      button(
        "restore v" + rev,
        function () {
          restoreRev(rev, function () {
            go(planDash(bench.key));
          });
        },
        { title: "a new version equal to v" + rev + "; nothing after it is lost" }
      )
    );
  }
  acts.appendChild(duplicateButton(rev));
  card.appendChild(acts);
  var shown = bench.viewPlan;
  if (bench.viewError) error(card, "v" + rev, bench.viewError);
  else if (!shown) loading(card, "v" + rev);
  else {
    card.appendChild(make("p", "", (shown.name !== plan.name ? "named “" + shown.name + "” · " : "") + argsWords(shown.args as unknown as Record<string, unknown>)));
    if (bench.viewDelta && bench.viewDelta.from_rev === rev) {
      card.appendChild(make("p", "dash-note", "result from v" + rev + " to v" + bench.viewDelta.to_rev + ": " + bench.viewDelta.text));
    }
  }
  parent.appendChild(card);
  if (bench.viewResult) renderVersionResult(parent, bench.viewResult, rev, select);
  else if (shown && !bench.viewError) loading(parent, "the result of v" + rev);
}

export function loadActivity(): void {
  var mine = ++activitySeq;
  var world = state.world;
  get<ActivityResponse>(`/api/activity?limit=${ACTIVITY_LIMIT}`)
    .then(function (data) {
      if (mine !== activitySeq) return;
      activity.world = world;
      activity.data = data;
      activity.error = "";
      changed();
    })
    .catch(function (reason) {
      if (mine !== activitySeq) return;
      activity.world = world;
      activity.error = friendlyError(reason);
      changed();
    });
}

function kept(row: ActivityRow): boolean {
  if (activity.filter === "you") return row.actor.kind === "page";
  if (activity.filter === "chat") return row.actor.kind === "chat";
  return true;
}

function sameRun(a: ActivityRow, b: ActivityRow): boolean {
  if (a.kind !== b.kind || a.actor.kind !== b.actor.kind) return false;
  if (a.kind === "world.find") return a.actor.pid === b.actor.pid;
  return a.kind === "plan.view" && a.plan === b.plan && a.text === b.text;
}

function collapsed(rows: ActivityRow[]): ActivityRow[] {
  var out: ActivityRow[] = [];
  rows.forEach(function (row) {
    var last = out[out.length - 1];
    if (last && sameRun(last, row)) out[out.length - 1] = { ...last, count: last.count + row.count };
    else out.push(row);
  });
  return out;
}

interface FindArgs {
  view?: string;
  params?: Record<string, string>;
}

function findDash(row: ActivityRow): string {
  var args = (row.args || {}) as FindArgs;
  var view = typeof args.view === "string" ? args.view : "";
  return withQuery(view ? "world/" + view : "world", args.params || {});
}

function what(row: ActivityRow): string {
  if (row.kind === "world.find" && row.count > 1) return counted(row.count, "find") + " · latest: " + row.text;
  if (row.source === "plan") return "v" + row.rev + " " + commitWords(row.text);
  return objectiveText(row.text);
}

function activityActs(row: ActivityRow): HTMLElement {
  var box = make("span", "dash-acts");
  if (row.source === "plan" && row.plan && row.rev && row.rev > 1) {
    var key = row.plan;
    var rev = row.rev;
    box.appendChild(
      button(
        "undo",
        function () {
          undoIn(key, rev)
            .then(function (text) {
              notify(text);
              loadActivity();
            })
            .catch(function (reason) {
              fail(friendlyError(reason));
            });
        },
        { title: "undo v" + rev + " as a new version", label: "undo v" + rev + " of " + (row.name || "the plan") }
      )
    );
  } else if (row.kind === "world.find") {
    var params = ((row.args || {}) as FindArgs).params || {};
    var open = link(findDash(row), "open");
    open.title = Object.keys(params).map(function (k) { return k + "=" + params[k]; }).join(" · ") || "open this World view";
    box.appendChild(open);
  } else if (row.kind === "plan.solve" && row.args) {
    box.appendChild(
      button(
        "open",
        function () {
          inbox.card = { ...row, world: state.world };
          changed();
          window.scrollTo(0, 0);
        },
        { title: "show chat's solve as a card: apply it, or make a new plan from it" }
      )
    );
  }
  return box;
}

export function renderActivity(parent: HTMLElement): void {
  var data = activity.world === state.world ? activity.data : null;
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "activity"));
  title.appendChild(
    subTabs(
      [
        { id: "all", label: "all" },
        { id: "you", label: WORDS.actorYou },
        { id: "chat", label: WORDS.actorChat },
      ],
      activity.filter,
      function (id) {
        activity.filter = id;
        changed();
      },
      "whose activity"
    )
  );
  card.appendChild(title);
  if (activity.error && activity.world === state.world) error(card, "the activity", activity.error, loadActivity);
  else if (!data) loading(card, "activity");
  else {
    var rows = collapsed(data.entries.filter(kept).reverse());
    if (!rows.length) empty(card, "nothing yet", "plan edits from the page and from chat, and chat's solves and finds, show here");
    else {
      var columns: Column<ActivityRow>[] = [
        {
          key: "when",
          label: "when",
          className: "dash-sub",
          render: function (r) {
            return ageShort(r.ts) + " ago";
          },
        },
        {
          key: "who",
          label: "by",
          render: function (r) {
            var who = make("span", "", actorWord(r.actor));
            if (r.actor.display) who.title = r.actor.display;
            return who;
          },
        },
        {
          key: "plan",
          label: "plan",
          render: function (r) {
            return r.plan ? link(planDash(r.plan), r.name || r.plan) : "–";
          },
        },
        { key: "what", label: "what", render: what },
        { key: "acts", label: "", render: activityActs },
      ];
      card.appendChild(table(columns, rows, { caption: "activity, newest first" }));
    }
  }
  parent.appendChild(card);
}
