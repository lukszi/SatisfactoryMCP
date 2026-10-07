/* Plan history on the page: the Versions list, a version viewed read-only, and the Activity panel.
 * See docs/plan_management.md. */

import { get } from "../../api/client";
import { button, empty, error, link, loading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { ageShort } from "../../kit/format";
import { go, withQuery } from "../../app/nav";
import { describeArgs } from "./chat-card";
import { actorWord, bench, changed, commitWords, inbox } from "./state";
import { duplicatePlan, restoreRev, undoRevisionOf } from "./writes";
import { renderVersionResult } from "./result";
import { state } from "../../app/state";
import { fail, friendlyError, notify } from "../../kit/toast";
import { counted, objectiveText, WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { ActivityResponse, ActivityRow, FocusSelection, VersionRow } from "../../api/shapes";

const ACTIVITY_LIMIT = 50;

const activity = {
  world: "",
  data: null as ActivityResponse | null,
  error: "",
  filter: "all",
};
let activitySeq = 0;

function planDash(key: string, rev?: number): string {
  return "planner/" + key + (rev ? "/v" + rev : "");
}

function duplicateAndOpen(rev?: number): void {
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
      duplicateAndOpen(rev);
    },
    { title: "a new plan at v1 with this version's request; this plan is untouched" }
  );
}

function versionStateText(row: VersionRow, head: number): string {
  const parts: string[] = [];
  if (row.rev === head) parts.push("head");
  if (row.undone_by) parts.push("undone in v" + row.undone_by);
  if (row.restores) parts.push("restores v" + row.restores);
  if (row.merged_over.length) parts.push("merged over " + row.merged_over.map(function (r) { return "v" + r; }).join(", "));
  return parts.join(" · ");
}

function versionActs(row: VersionRow, head: number): HTMLElement {
  const box = make("span", "dash-acts");
  const key = bench.key;
  box.appendChild(
    button(
      "view",
      function () {
        go(planDash(key, row.rev));
      },
      { title: "open v" + row.rev + " read-only", label: "view v" + row.rev, disabled: row.rev === bench.viewedRev }
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
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "versions"));
  const data = bench.versions;
  if (bench.versionsError) error(card, "the versions", bench.versionsError);
  else if (data?.key !== bench.key) loading(card, "versions");
  else {
    const head = data.head;
    const columns: Column<VersionRow>[] = [
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
          return versionStateText(r, head);
        },
      },
    ];
    card.appendChild(
      table(columns, data.versions, {
        rowClass: function (r) {
          return r.rev === bench.viewedRev ? "plan-picked" : "";
        },
        caption: "versions of this plan",
      })
    );
    card.appendChild(make("p", "dash-note", "restore adds a new version equal to the old one; view opens a version read-only"));
  }
  parent.appendChild(card);
}

export function renderRevisionView(parent: HTMLElement, select: (s: FocusSelection) => void): void {
  const rev = bench.viewedRev;
  const plan = bench.plan!;
  const card = make("section", "dash-card plan-strip");
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "v" + rev + " · read-only"));
  title.appendChild(make("span", "plan-status", "the plan is at v" + plan.rev));
  card.appendChild(title);
  const acts = make("div", "dash-acts");
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
  const shown = bench.viewedPlan;
  if (bench.viewedError) error(card, "v" + rev, bench.viewedError);
  else if (!shown) loading(card, "v" + rev);
  else {
    card.appendChild(make("p", "", (shown.name !== plan.name ? "named “" + shown.name + "” · " : "") + describeArgs(shown.args as unknown as Record<string, unknown>)));
    if (bench.viewedDelta?.from_rev === rev) {
      card.appendChild(make("p", "dash-note", "result from v" + rev + " to v" + bench.viewedDelta.to_rev + ": " + bench.viewedDelta.text));
    }
  }
  parent.appendChild(card);
  if (bench.viewedResult) renderVersionResult(parent, bench.viewedResult, rev, select);
  else if (shown && !bench.viewedError) loading(parent, "the result of v" + rev);
}

export function loadActivity(): void {
  const mine = ++activitySeq;
  const world = state.world;
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

function matchesActivityFilter(row: ActivityRow): boolean {
  if (activity.filter === "you") return row.actor.kind === "page";
  if (activity.filter === "chat") return row.actor.kind === "chat";
  return true;
}

function sameRun(a: ActivityRow, b: ActivityRow): boolean {
  if (a.kind !== b.kind || a.actor.kind !== b.actor.kind) return false;
  if (a.kind === "world.find") return a.actor.pid === b.actor.pid;
  return a.kind === "plan.view" && a.plan === b.plan && a.text === b.text;
}

function collapseRuns(rows: ActivityRow[]): ActivityRow[] {
  const out: ActivityRow[] = [];
  rows.forEach(function (row) {
    const last = out[out.length - 1];
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
  const args = (row.args || {}) as FindArgs;
  const view = typeof args.view === "string" ? args.view : "";
  return withQuery(view ? "world/" + view : "world", args.params || {});
}

function activityText(row: ActivityRow): string {
  if (row.kind === "world.find" && row.count > 1) return counted(row.count, "find") + " · latest: " + row.text;
  if (row.source === "plan") return "v" + row.rev + " " + commitWords(row.text);
  return objectiveText(row.text);
}

function activityActs(row: ActivityRow): HTMLElement {
  const box = make("span", "dash-acts");
  if (row.source === "plan" && row.plan && row.rev && row.rev > 1) {
    const key = row.plan;
    const rev = row.rev;
    box.appendChild(
      button(
        "undo",
        function () {
          undoRevisionOf(key, rev)
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
    const params = ((row.args || {}) as FindArgs).params || {};
    const open = link(findDash(row), "open");
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
  const data = activity.world === state.world ? activity.data : null;
  const card = make("section", "dash-card");
  const title = make("div", "dash-title");
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
    const rows = collapseRuns(data.entries.filter(matchesActivityFilter).reverse());
    if (!rows.length) empty(card, "nothing yet", "plan edits from the page and from chat, and chat's solves and finds, show here");
    else {
      const columns: Column<ActivityRow>[] = [
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
            const who = make("span", "", actorWord(r.actor));
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
        { key: "what", label: "what", render: activityText },
        { key: "acts", label: "", render: activityActs },
      ];
      card.appendChild(table(columns, rows, { caption: "activity, newest first" }));
    }
  }
  parent.appendChild(card);
}
