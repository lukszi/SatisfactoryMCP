/* The card a chat solve leaves on the planner: what chat asked for, and applying it to this plan,
 * opening the plan chat used, or saving it as a new plan. */

import { button } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { perMin } from "../../kit/format";
import { go } from "../../app/nav";
import { loadList, planTitle } from "./planner-plan-index";
import { hours } from "./planner-power";
import { bench, changed, inbox } from "./planner-state";
import { applyArgs, createPlan } from "./planner-writes";
import { fail, friendlyError } from "../../kit/toast";
import { counted, OBJECTIVES, objectiveText } from "../../kit/words";

/** A plan request in one line: goal, exports, sources, recipe lists and power settings. */
export function describeArgs(args: Record<string, unknown>): string {
  var parts: string[] = [];
  var objective = args.objective as string | undefined;
  if (objective) parts.push("goal " + (OBJECTIVES[objective] || objective) + (args.target_item ? " of " + args.target_item : ""));
  var mins = (args.export_minimums as Record<string, number> | undefined) || {};
  var exported = ((args.exports as string[] | undefined) || []).slice();
  Object.keys(mins).forEach(function (item) {
    if (exported.indexOf(item) < 0) exported.push(item);
  });
  if (exported.length) {
    parts.push(
      "exports " +
        exported
          .map(function (item) {
            return item in mins ? item + " " + perMin(mins[item]!) : item;
          })
          .join(", ")
    );
  }
  var from = (args.sources as string[] | undefined) || [];
  var nodes = from.filter(function (source) {
    return source.indexOf("node:") === 0;
  }).length;
  if (!from.length) parts.push("from the whole map");
  else if (nodes === from.length) parts.push("from " + counted(nodes, "node"));
  else if (from.length < 3) parts.push("from " + from.join(", "));
  else parts.push("from " + counted(from.length, "selector"));
  var banned = ((args.exclude_recipes || args.banned) as string[] | undefined) || [];
  if (banned.length) parts.push(counted(banned.length, "banned recipe"));
  var required = (args.required as string[] | undefined) || [];
  if (required.length) parts.push(counted(required.length, "required recipe"));
  if (args.sloops) parts.push(counted(Number(args.sloops), "somersloop"));
  if (typeof args.payback_hours === "number") parts.push("payback " + hours(args.payback_hours));
  if (args.overclock_last === true) parts.push("overclock last");
  return parts.join(" · ");
}

/** A name for a plan made from chat's request: its first export at its rate, else chat's words. */
function chatName(args: Record<string, unknown>, fallback: string): string {
  var mins = args.export_minimums as Record<string, number> | undefined;
  var items = mins ? Object.keys(mins) : [];
  if (items.length) return items[0] + " " + perMin(mins![items[0]!]!);
  return fallback.slice(0, 40) || "chat solve";
}

export function renderChatSolveCard(parent: HTMLElement): void {
  var entry = inbox.card;
  if (!entry) return;
  var card = make("section", "dash-card plan-strip");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "chat solved this; nothing is saved yet"));
  card.appendChild(title);
  card.appendChild(make("p", "", objectiveText(entry.text)));
  var args = entry.args;
  if (args) card.appendChild(make("p", "dash-note", describeArgs(args)));
  var acts = make("div", "dash-acts");
  var elsewhere = entry.plan && entry.plan !== bench.key ? entry.plan : "";
  if (elsewhere) {
    var target = elsewhere;
    var called = planTitle(target) || entry.name;
    if (!called) loadList();
    acts.appendChild(
      button(
        "open " + (called ? "“" + called + "”" : "the plan chat used"),
        function () {
          go("planner/" + target);
        },
        { title: "open the plan chat solved from; apply it there to merge with its edits" }
      )
    );
  }
  if (bench.plan && args && !bench.gone) {
    var from = entry.plan === bench.key && typeof entry.rev === "number" ? entry.rev : undefined;
    acts.appendChild(
      button(
        from === undefined ? "replace this plan's request" : "apply to this plan",
        function () {
          inbox.card = null;
          applyArgs(args!, entry!.id, from);
        },
        {
          title:
            from === undefined
              ? "chat did not solve from this plan: replace its whole request with chat's, as one new version"
              : "apply what chat changed from v" + from + ", merged with edits made since",
        }
      )
    );
  }
  if (args) {
    acts.appendChild(
      button(
        "new plan from it",
        function () {
          inbox.card = null;
          changed();
          createPlan(chatName(args!, entry!.text), args!, entry!.id)
            .then(function (key) {
              go("planner/" + key);
            })
            .catch(function (reason) {
              fail(friendlyError(reason));
            });
        },
        { title: "save chat's request as a new plan and open it" }
      )
    );
  }
  acts.appendChild(
    button(
      "dismiss",
      function () {
        inbox.card = null;
        changed();
      },
      { title: "forget this card" }
    )
  );
  card.appendChild(acts);
  parent.appendChild(card);
}
