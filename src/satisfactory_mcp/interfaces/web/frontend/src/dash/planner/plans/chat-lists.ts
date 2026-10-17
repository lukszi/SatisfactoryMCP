/* What chat left on the Planner page, in the kit: the solve card, the pins and the asks, one
 * line per row (name and context, a state chip, the actions in ⋯). docs/frontend_kit.md,
 * "Planner". */

import { askLabel, askStore, dropAsk, liveAsks, loadAsks, openAsk, refetchAsks } from "../../../chat/asks";
import { dropPin, LABEL_MAX, pinStore, refetchPins, renamePin, showPin } from "../../../chat/pins";
import { go } from "../../../app/nav";
import { copyText } from "../../../kit/copy";
import { isRebuilding } from "../../../kit/focus";
import { timeOfDay } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { openMenu } from "../../../kit/overlay/menu";
import { fail, friendlyError, notify } from "../../../kit/toast";
import { btn, glyphBtn, moreBtn } from "../../../kit/ui/controls";
import { acts, entry, list, nameLine } from "../../../kit/ui/entry";
import { chip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine, toolrow } from "../../../kit/ui/text";
import { ASK_STATE, objectiveText } from "../../../kit/words";
import { hashFor } from "../../../map/map";
import { chatName, describeParts } from "../chat-card";
import { loadList, planTitle } from "../plan-index";
import { changed, inbox } from "../state";
import { createPlan } from "../writes";

import type { AskAbout, AskRow, PinRow } from "../../../api/shapes";

const PINS_SHOWN = 50;
const PIN_MENU = ["rename", "copy", "ask chat", "delete"];
const ASK_MENU = ["copy", "delete"];

const view = { allPins: false, renaming: 0 };

/* ------------------------------------------------------------------ the solve */

/** Chat's last solve, nothing saved yet: what it asked for, then open, save or dismiss. */
export function solveCard(): Safe {
  const card = inbox.card;
  if (!card) return html``;
  const called = card.plan ? planTitle(card.plan) || card.name : "";
  if (card.plan && !called) loadList();
  const open = card.plan && btn("open " + (called ? "“" + called + "”" : "its plan"), { title: "open the plan chat solved from; apply it there to merge with its edits", data: { cs: "open" } });
  const save = card.args && btn("new plan from it", { title: "save chat's request as a new plan and open it", data: { cs: "new" } });
  const parts = card.args ? describeParts(card.args) : [];
  return list(
    "csolve",
    "stacked",
    entry(
      { title: parts.join(" · ") },
      html`<div class="id">${nameLine(html`<span class="name">chat solved this</span>`, objectiveText(card.text))}${parts.length ? html`<div class="chips">${parts.map((p) => chip(p, "muted"))}</div>` : ""}</div>${chip("not saved", "mid")}${acts(html`${open}${save}${glyphBtn("×", "dismiss", { extra: "cs-x" })}`)}`
    ),
    "nolead"
  );
}

function solveAction(what: string): void {
  const card = inbox.card;
  if (!card) return;
  if (what === "open" && card.plan) go("planner/" + card.plan);
  else if (what === "new" && card.args) {
    const args = card.args;
    inbox.card = null;
    changed();
    createPlan(chatName(args, card.text), args, card.id)
      .then((key) => go("planner/" + key))
      .catch((reason) => fail(friendlyError(reason)));
  } else if (what === "dismiss") {
    inbox.card = null;
    changed();
  }
}

/* ------------------------------------------------------------------ pins */

function pinWhere(pin: PinRow): Safe {
  if (pin.x_m !== null && pin.y_m !== null) return html`<button type="button" class="btn icon" data-pinmap="${String(pin.n)}" title="${"show " + pin.id + " on the map"}" aria-label="${"show " + pin.id + " on the map"}">◎</button>`;
  if (pin.ref.plan && !pin.gone) return html`<a class="btn icon" href="${hashFor("planner/" + pin.ref.plan)}" title="open the plan" aria-label="${"open the plan of " + pin.id}">→</a>`;
  return html`<span class="btn-gap" aria-hidden="true"></span>`;
}

function pinName(pin: PinRow): Safe {
  if (view.renaming === pin.n) return html`<input type="text" class="prename" id="pinrename" data-pin="${String(pin.n)}" value="${pin.label}" maxlength="${LABEL_MAX}" aria-label="${"label for " + pin.id}" placeholder="label">`;
  return nameLine(html`${chip(pin.id, "id")} <span class="name">${pin.label || pin.text}</span>`, pin.label ? pin.text : "");
}

function pinRow(pin: PinRow): Safe {
  const state = pin.gone ? chip("gone", "muted", pin.gone_why) : html`<span></span>`;
  return entry(
    { title: pin.id + " · " + pin.text + (pin.gone ? " · gone: " + pin.gone_why : "") },
    html`<div class="id">${pinName(pin)}</div>${state}${acts(html`${pinWhere(pin)}<span data-pin="${String(pin.n)}">${moreBtn(pin.id, PIN_MENU)}</span>`)}`
  );
}

export function pinsBlock(): Safe {
  const got = pinStore();
  const rows = got.data ? got.data.pins.slice().sort((a, b) => a.n - b.n) : [];
  const head = toolrow(dashH("pins", got.data ? rows.length : undefined, "h2", "pin a plan, a process or a place, then name pin:N to chat"));
  if (got.error && !got.data) return html`${head}${emptyLine("the pins did not load", got.error)} ${btn("try again", { data: { cs: "pins-retry" } })}`;
  if (!got.data) return html`${head}${loadingLine("pins")}`;
  if (!rows.length) return html`${head}${emptyLine("no pins yet", "pin a plan, a process, or a place on the map")}`;
  const shown = view.allPins ? rows : rows.slice(0, PINS_SHOWN);
  const more = rows.length > shown.length ? btn("show all " + rows.length, { data: { cs: "pins-all" } }) : "";
  return html`${head}${list("cpins", "stacked", shown.map(pinRow), "nolead")}${more}`;
}

function pinOf(n: string | undefined): PinRow | undefined {
  return pinStore().data?.pins.filter((p) => String(p.n) === n)[0];
}

function copied(text: string, what: string): void {
  copyText(text).then(
    () => notify("copied " + what),
    () => fail("the browser refused to copy " + what)
  );
}

function pinAction(label: string, pin: PinRow): void {
  if (label === "rename") {
    view.renaming = pin.n;
    changed();
  } else if (label === "copy") copied(pin.id, pin.id);
  else if (label === "ask chat") {
    const about: AskAbout = { kind: "pin", label: pin.id + " " + (pin.label || pin.text.replace(/ in “[^”]*”$/, "")), ref: pin.id };
    if (pin.ref.plan && !pin.gone) about.plan = pin.ref.plan;
    openAsk(about, "pin:" + pin.n);
  } else if (label === "delete") dropPin(pin);
}

function commitPinLabel(input: HTMLInputElement): void {
  const pin = pinOf(input.dataset.pin);
  const text = input.value.trim();
  view.renaming = 0;
  input.defaultValue = input.value;
  if (!pin || text === pin.label) return changed();
  if (text.length > LABEL_MAX) {
    fail("a label is at most " + LABEL_MAX + " characters");
    return changed();
  }
  renamePin(pin, text).then(changed);
}

/* ------------------------------------------------------------------ asks */

function askAbout(ask: AskRow): Safe {
  const plan = ask.about.plan;
  const named = plan && ask.plan_name ? html`<a href="${hashFor("planner/" + plan)}">“${ask.plan_name}”</a>` : "";
  if (ask.about.kind === "plan" && named) return html`plan ${named}`;
  if (!plan || ask.about.kind === "plan") return html`${askLabel(ask.about)}`;
  return html`${askLabel(ask.about)} in ${named || html`<span class="dim">a forgotten plan</span>`}`;
}

function askState(ask: AskRow): Safe {
  if (ask.state === "answered" && ask.answered !== null) return chip(ASK_STATE.answered + " " + timeOfDay(ask.answered), "muted", ask.answered_by ? "marked answered by " + ask.answered_by : "");
  if (ask.state === "seen" && ask.seen !== null) return chip(ASK_STATE.seen + " " + timeOfDay(ask.seen), "muted", ask.seen_by ? "read by " + ask.seen_by : "");
  return chip(ASK_STATE.open || ask.state, "mid", "queued " + timeOfDay(ask.created) + "; paste " + ask.id + " into chat");
}

function askRow(ask: AskRow): Safe {
  const answer = ask.answer ? html`<div class="ctx" title="${ask.answer}">answer: ${ask.answer}</div>` : "";
  return entry(
    { title: ask.text },
    html`<div class="id">${nameLine(html`${chip(ask.id, "id")} <span class="name">${ask.text}</span>`, askAbout(ask))}${answer}</div>${askState(ask)}${acts(html`<span data-ask="${String(ask.n)}">${moreBtn(ask.id, ASK_MENU)}</span>`)}`
  );
}

export function asksBlock(): Safe {
  loadAsks();
  const got = askStore();
  const rows = got.data ? liveAsks().slice().sort((a, b) => b.n - a.n) : [];
  const head = toolrow(dashH("asks", got.data ? rows.length : undefined, "h2", "questions queued for chat from a plan's rows; paste ask:N into chat"));
  if (got.error && !got.data) return html`${head}${emptyLine("the asks did not load", got.error)} ${btn("try again", { data: { cs: "asks-retry" } })}`;
  if (!got.data) return html`${head}${loadingLine("asks")}`;
  if (!rows.length) return html`${head}${emptyLine("no asks yet", "ask chat from any row of a plan")}`;
  return html`${head}${list("casks", "stacked", rows.map(askRow), "nolead")}`;
}

function askOf(n: string | undefined): AskRow | undefined {
  return liveAsks().filter((a) => String(a.n) === n)[0];
}

/* ------------------------------------------------------------------ wiring */

/** The solve card's buttons, the pins' and asks' ⋯ menus, ◎ and the pin label field. */
export function wireChatLists(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const cs = target.closest<HTMLElement>("[data-cs]");
    if (cs) {
      const what = cs.dataset.cs!;
      if (what === "pins-all") {
        view.allPins = true;
        changed();
      } else if (what === "pins-retry") refetchPins();
      else if (what === "asks-retry") refetchAsks();
      else solveAction(what);
      return;
    }
    if (target.closest(".cs-x")) return solveAction("dismiss");
    const map = target.closest<HTMLElement>("[data-pinmap]");
    if (map) {
      const pin = pinOf(map.dataset.pinmap);
      if (pin) showPin(pin);
      return;
    }
    const more = target.closest<HTMLElement>(".more[data-menu]");
    const pin = more && pinOf(more.closest<HTMLElement>("[data-pin]")?.dataset.pin);
    if (more && pin) return openMenu(more, PIN_MENU.map((label) => ({ label, act: () => pinAction(label, pin) })));
    const ask = more && askOf(more.closest<HTMLElement>("[data-ask]")?.dataset.ask);
    if (more && ask) openMenu(more, ASK_MENU.map((label) => ({ label, act: () => (label === "copy" ? copied(ask.copy, ask.id) : dropAsk(ask)) })));
  });
  root.addEventListener("keydown", function (event) {
    const input = event.target as HTMLInputElement;
    if (input.id !== "pinrename") return;
    if (event.key === "Enter") {
      event.preventDefault();
      commitPinLabel(input);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      view.renaming = 0;
      changed();
    }
  });
  root.addEventListener("focusout", function (event) {
    const input = event.target as HTMLInputElement;
    if (input.id === "pinrename" && view.renaming && !isRebuilding()) commitPinLabel(input);
  });
}

/** Puts the caret in the pin label field once it is drawn. */
export function focusPinRename(root: HTMLElement): void {
  const field = root.querySelector<HTMLInputElement>("#pinrename");
  if (field && document.activeElement !== field) {
    field.focus();
    field.select();
  }
}
