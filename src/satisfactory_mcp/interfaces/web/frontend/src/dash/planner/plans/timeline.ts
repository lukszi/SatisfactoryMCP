/* The Planner page's activity timeline: plan versions and chat's solves, views and finds from
 * /api/activity, by day on one rail, with undo or open where the API offers one. */

import { get } from "../../../api/client";
import { state } from "../../../app/state";
import { busyButton } from "../../../kit/busy";
import { timeOfDay } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { btn, seg } from "../../../kit/ui/controls";
import { chip } from "../../../kit/ui/marks";
import { dashH, emptyLine, toolrow } from "../../../kit/ui/text";
import { fail, friendlyError, notify } from "../../../kit/toast";
import { counted, objectiveText, WORDS } from "../../../kit/words";
import { hashFor } from "../../../map/map";
import { fromLegacy } from "../../world/address";
import { loadList } from "../plan-index";
import { actorWord, changed, commitWords, inbox } from "../state";
import { undoRevisionOf } from "../writes";
import { activityLines, byDay, whoOf, type Who } from "./model";

import type { ActivityResponse, ActivityRow } from "../../../api/shapes";

const LIMIT = 50;

const activity = {
  world: "",
  data: null as ActivityResponse | null,
  error: "",
  who: "all" as Who,
};
let seq = 0;
/* The solve rows "open" can show, by id, from the last render. */
const solves = new Map<string, ActivityRow>();

export function loadActivity(): void {
  const mine = ++seq;
  const world = state.world;
  get<ActivityResponse>(`/api/activity?limit=${LIMIT}`)
    .then(function (data) {
      if (mine !== seq) return;
      activity.world = world;
      activity.data = data;
      activity.error = "";
      changed();
    })
    .catch(function (reason) {
      if (mine !== seq) return;
      activity.world = world;
      activity.error = friendlyError(reason);
      changed();
    });
}

interface FindArgs {
  view?: string;
  params?: Record<string, string>;
}

function findDash(row: ActivityRow): string {
  const args = (row.args || {}) as FindArgs;
  return typeof args.view === "string" ? fromLegacy(args.view, args.params || {}) : "world";
}

function whatText(row: ActivityRow): string {
  if (row.kind === "world.find" && row.count > 1) return counted(row.count, "find") + " · latest: " + row.text;
  if (row.source === "plan") return "v" + row.rev + " " + commitWords(row.text);
  return objectiveText(row.text);
}

function action(row: ActivityRow): Safe {
  if (row.source === "plan" && row.plan && row.rev && row.rev > 1) {
    return btn("undo", { title: "undo v" + row.rev + " of " + (row.name || "the plan") + " as a new version", data: { undo: row.plan + "|" + row.rev } });
  }
  if (row.kind === "world.find") return html`<a class="btn" href="${hashFor(findDash(row))}" title="open this World view">open</a>`;
  if (row.kind === "plan.solve" && row.args) {
    solves.set(row.id, row);
    return btn("open", { title: "show chat's solve as a card: apply it, or make a new plan from it", data: { solve: row.id } });
  }
  return html`<span></span>`;
}

function line(row: ActivityRow): Safe {
  const who = whoOf(row);
  const plan = row.plan ? html`<a class="ictx" href="${hashFor("planner/" + row.plan)}">${row.name || row.plan}</a>` : "";
  const times = row.kind === "plan.view" && row.count > 1 ? html` <span class="ictx">×${row.count}</span>` : "";
  const actor = chip(actorWord(row.actor), "muted", row.actor.display);
  return html`<li class="tl-ev ${who}"><i class="tl-dot" aria-hidden="true"></i><time>${timeOfDay(row.ts)}</time>${actor}<span class="nl"><span class="line1" title="${whatText(row)}">${whatText(row)}</span><span class="tl-who">${actor}</span>${times}${plan}</span>${action(row)}</li>`;
}

const WHO_ITEMS = [
  { value: "all", label: "all" },
  { value: "you", label: WORDS.actorYou },
  { value: "chat", label: WORDS.actorChat },
];

export function timeline(): Safe {
  const data = activity.world === state.world ? activity.data : null;
  const rows = data ? activityLines(data.entries, activity.who) : [];
  const head = toolrow(dashH("activity", data ? rows.length : undefined), seg("act", WHO_ITEMS, activity.who, "whose activity"));
  solves.clear();
  if (activity.error && activity.world === state.world) return html`${head}${emptyLine("the activity did not load", activity.error)}`;
  if (!data) return html`${head}<p class="note" aria-busy="true">loading activity…</p>`;
  if (!rows.length) {
    return html`${head}${activity.who === "all" ? emptyLine("nothing yet", "plan edits from the page and from chat, and chat's solves and finds, show here") : emptyLine("no activity by " + activity.who)}`;
  }
  const days = byDay(rows, Date.now()).map((d) => html`<h3 class="tl-day">${d.day}</h3><ol class="tl">${d.rows.map(line)}</ol>`);
  return html`${head}${days}`;
}

/** The filter, undo and open inside `root`. */
export function wireTimeline(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const pick = target.closest<HTMLElement>('[data-seg="act"]');
    if (pick) {
      activity.who = (pick.dataset.val || "all") as Who;
      changed();
      return;
    }
    const undo = target.closest<HTMLButtonElement>("[data-undo]");
    if (undo) {
      const [key, rev] = undo.dataset.undo!.split("|");
      busyButton(undo, () => undoRevisionOf(key!, Number(rev)))
        .then(function (text) {
          notify(text);
          loadList();
          loadActivity();
        })
        .catch((reason) => fail(friendlyError(reason)));
      return;
    }
    const solve = target.closest<HTMLElement>("[data-solve]");
    const row = solve ? solves.get(solve.dataset.solve!) : undefined;
    if (row) {
      inbox.card = { ...row, world: state.world };
      changed();
      document.getElementById("dash")?.scrollTo(0, 0);
    }
  });
}
