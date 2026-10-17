/* The versions tab: the Planner's timeline for one plan, a line per version with who, what,
 * what it changed in the result against the version before (/api/plan/delta), view and
 * restore. View opens the version read-only on the machines tab. */

import { go } from "../../../app/nav";
import { busyButton } from "../../../kit/busy";
import { timeOfDay } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { seg } from "../../../kit/ui/controls";
import { chip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine, toolrow } from "../../../kit/ui/text";
import { WORDS } from "../../../kit/words";
import { delta, loadVersions } from "../reads";
import { versionDash } from "../routes";
import { actorWord, bench, changed, commitWords } from "../state";
import { restoreRev, writesSettled } from "../writes";
import { byDayOf } from "./model";
import { view } from "./parts";

import type { VersionRow } from "../../../api/shapes";

/** Deltas read so far (key:rev → its text vs the version before); "" while asked or failed. */
const deltas: Record<string, string> = {};
const waiting: [string, number][] = [];
let reading = false;
const DELTA_ROWS = 30;

function pump(): void {
  if (reading) return;
  const next = waiting.shift();
  if (!next) return;
  reading = true;
  const [key, rev] = next;
  delta(key, rev - 1, rev)
    .then((d) => (deltas[key + ":" + rev] = d.text))
    .catch(() => undefined)
    .then(function () {
      reading = false;
      changed();
      pump();
    });
}

function deltaOf(rev: number): string {
  const id = bench.key + ":" + rev;
  if (rev <= 1) return "";
  if (!(id in deltas)) {
    deltas[id] = "";
    waiting.push([bench.key, rev]);
    pump();
  }
  return deltas[id]!;
}

function whoOf(row: VersionRow): string {
  return row.actor.kind === "page" ? "you" : row.actor.kind === "chat" ? "chat" : "other";
}

function line(row: VersionRow, head: number, withDelta: boolean): Safe {
  const chips = html`${row.undoes ? chip("undoes v" + row.undoes, "muted") : ""}${row.undone_by ? chip("undone in v" + row.undone_by, "mid") : ""}${row.restores ? chip("restores v" + row.restores, "muted") : ""}${row.merged_over.length ? chip("merged over " + row.merged_over.map((r) => "v" + r).join(", "), "muted") : ""}`;
  const said = withDelta ? deltaOf(row.rev) : "";
  const actions =
    row.rev === head
      ? chip("current", "muted")
      : html`<span class="acts"><button type="button" class="btn" data-pv="view" data-rev="${String(row.rev)}" title="${"open v" + row.rev + " read-only"}">view</button>${bench.gone ? "" : html`<button type="button" class="btn hide-phone" data-pv="restore" data-rev="${String(row.rev)}" title="${"a new version equal to v" + row.rev + "; nothing after it is lost"}"><span class="spinner" aria-hidden="true"></span>restore</button>`}</span>`;
  return html`<li class="${"tl-ev " + whoOf(row) + (row.undone_by ? " undone" : "")}"${row.note ? html` title="${row.note}"` : ""}><i class="tl-dot" aria-hidden="true"></i><time>${timeOfDay(row.ts)}</time>${chip(actorWord(row.actor), "muted", row.actor.display)}<span class="nl"><b class="rev">v${row.rev}</b> <span class="line1">${commitWords(row.text)}</span>${chips}${said ? html`<span class="ictx">${said}</span>` : ""}</span>${actions}</li>`;
}

const WHO = [
  { value: "all", label: "all" },
  { value: "you", label: WORDS.actorYou },
  { value: "chat", label: WORDS.actorChat },
];

export function versionsTab(): Safe {
  const data = bench.versions?.key === bench.key ? bench.versions : null;
  const rows = data ? data.versions.slice().sort((a, b) => b.rev - a.rev).filter((r) => view.versionsWho === "all" || whoOf(r) === view.versionsWho) : [];
  const head = toolrow(dashH("versions", data ? rows.length : undefined), seg("pver", WHO, view.versionsWho, "whose versions"));
  if (bench.versionsError && !data) return html`${head}${emptyLine("the versions could not be read", bench.versionsError)} <button type="button" class="btn" data-pv="retry">try again</button>`;
  if (!data) return html`${head}${loadingLine("versions")}`;
  if (!rows.length) return html`${head}${emptyLine("no versions by " + view.versionsWho)}`;
  let shown = 0;
  const days = byDayOf(rows, Date.now()).map(
    (d) =>
      html`<h3 class="tl-day">${d.day}</h3><ol class="tl pver">${d.rows.map((r) => {
        shown++;
        return line(r, data.head, shown <= DELTA_ROWS);
      })}</ol>`
  );
  return html`${head}${days}`;
}

export function versionsClick(target: Element): boolean {
  const who = target.closest<HTMLElement>('[data-seg="pver"]');
  if (who) {
    view.versionsWho = who.dataset.val || "all";
    changed();
    return true;
  }
  const b = target.closest<HTMLElement>("[data-pv]");
  if (!b) return false;
  const rev = Number(b.dataset.rev);
  if (b.dataset.pv === "retry") loadVersions();
  else if (b.dataset.pv === "view") go(versionDash(bench.key, rev));
  else if (b.dataset.pv === "restore")
    busyButton(b as HTMLButtonElement, function () {
      restoreRev(rev);
      return writesSettled();
    }).catch(() => undefined);
  return true;
}
