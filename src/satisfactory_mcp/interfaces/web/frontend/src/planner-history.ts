/* Plan history on the page: the Versions list, a version viewed read-only, and the Activity panel.
 * See docs/plan_management.md. */

import { get } from "./api";
import { button, chip, empty, error, link, loading, table, tabs2 } from "./dashkit";
import { make } from "./dom";
import { go } from "./nav";
import { argsWords } from "./planner-bench";
import { actorWord, age, bench, changed, commitWords, duplicatePlan, inbox, restoreRev, undoIn } from "./planner-core";
import { renderVersionResult } from "./planner-result";
import { state } from "./state";
import { fail, friendly, note } from "./toast";
import { W } from "./words";

import type { Column } from "./dashkit";
import type { ActivityResponse, ActivityRow, VersionRow } from "./api-shapes";
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
      note("copied to a new plan");
      go(planDash(key));
    })
    .catch(function (reason) {
      fail(friendly(reason));
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
          return age(r.ts) + " ago";
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
      { key: "acts", label: "", render: function (r) { return versionActs(r, head); } },
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
      activity.error = friendly(reason);
      changed();
    });
}

function kept(row: ActivityRow): boolean {
  if (activity.filter === "you") return row.actor.kind === "page";
  if (activity.filter === "chat") return row.actor.kind === "chat";
  return true;
}

function what(row: ActivityRow): string {
  if (row.source === "plan") return "v" + row.rev + " " + commitWords(row.text);
  return row.text;
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
              note(text);
              loadActivity();
            })
            .catch(function (reason) {
              fail(friendly(reason));
            });
        },
        { title: "undo v" + rev + " as a new version", label: "undo v" + rev + " of " + (row.name || "the plan") }
      )
    );
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
  if (activity.world !== state.world && activity.data) {
    activity.data = null;
    loadActivity();
  }
  if (!activity.data && !activity.error && activitySeq === 0) loadActivity();
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "activity"));
  title.appendChild(
    tabs2(
      [
        { id: "all", label: "all" },
        { id: "you", label: W.actorYou },
        { id: "chat", label: W.actorChat },
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
  if (activity.error) error(card, "the activity", activity.error, loadActivity);
  else if (!activity.data) loading(card, "activity");
  else {
    var rows = activity.data.entries.filter(kept).reverse();
    if (!rows.length) empty(card, "nothing yet", "plan edits from the page and from chat, and chat's solves, show here");
    else {
      var columns: Column<ActivityRow>[] = [
        {
          key: "when",
          label: "when",
          className: "dash-sub",
          render: function (r) {
            return age(r.ts) + " ago";
          },
        },
        {
          key: "who",
          label: "by",
          render: function (r) {
            return r.actor.kind === "page" || r.actor.kind === "chat" ? chip(actorWord(r.actor), "muted", r.actor.display) : actorWord(r.actor);
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
