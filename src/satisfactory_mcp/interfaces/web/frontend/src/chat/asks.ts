/* Asks: questions queued on the page for chat as ask:N, the one ask bar, and the store.
 * See docs/planner-p4_contract.md §2 F6 and §4. */

import { send } from "../api/client";
import { copyText } from "../kit/copy";
import { button, fieldError } from "../kit/dashkit";
import { make } from "../kit/dom";
import { liveStore } from "./livestore";
import { fail, friendlyError, notify } from "../kit/toast";
import { ASK_KIND, WORDS } from "../kit/words";

import type { ApiError, ApiPath, StatusError } from "../api/client";
import type { AskAbout, AskDropped, AskRow, AsksResponse, AskStaleResponse } from "../api/shapes";

export const ASK_MAX = 200;

const LABEL_MAX = 120;
const REF_MAX = 200;
const ASKS_PATH: ApiPath = "/api/asks";
const ASK_PATH: ApiPath = "/api/asks/{n}";
const TEXT_CTL = "ask-text";

const store = liveStore<AsksResponse, AskRow>(ASKS_PATH, function (data) {
  return data.asks;
}, "ask");
const bar = { about: null as AskAbout | null, ownerId: "", text: "", validationError: "", focusInputOnRender: false, returnFocusTo: "", busy: false };

export const onAsks = store.on;
export const askStore = store.read;
export const refetchAsks = store.refetch;
export const loadAsks = store.load;
const notifyAskListeners = store.notify;

export function liveAsks(): AskRow[] {
  const data = askStore().data;
  return data ? data.asks : [];
}

export function asksFor(planKey: string): AskRow[] {
  return liveAsks().filter(function (ask) {
    return ask.about.plan === planKey;
  });
}

/** The unanswered asks about one thing in a plan, for the marks beside it. */
export function openAsksAbout(planKey: string, kind: string, ref: string): AskRow[] {
  return asksFor(planKey).filter(function (ask) {
    return ask.about.kind === kind && ask.about.ref === ref && ask.state !== "answered";
  });
}

function truncateChars(text: string, max: number): string {
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

export function askLabel(about: AskAbout): string {
  return (ASK_KIND[about.kind] || about.kind) + " “" + about.label + "”";
}

export function askButton(about: AskAbout, ctl: string, text?: string): HTMLButtonElement {
  const subject: AskAbout = { kind: about.kind, label: truncateChars(about.label, LABEL_MAX), ref: truncateChars(about.ref, REF_MAX) };
  if (about.plan) subject.plan = about.plan;
  if (about.rev) subject.rev = about.rev;
  const id = "ask:" + ctl;
  const askBtn = button(
    text || "ask",
    function () {
      openBar(subject, id);
    },
    { title: "queue a question for chat about this " + (ASK_KIND[about.kind] || about.kind), label: "ask chat about " + askLabel(subject) }
  );
  askBtn.setAttribute("data-ctl", id);
  askBtn.setAttribute("aria-expanded", String(bar.ownerId === id && !!bar.about));
  return askBtn;
}

function openBar(about: AskAbout, ownerId: string): void {
  bar.about = about;
  bar.ownerId = ownerId;
  bar.validationError = "";
  bar.focusInputOnRender = true;
  bar.returnFocusTo = "";
  notifyAskListeners();
}

export function isAskBarOpen(): boolean {
  return !!bar.about;
}

/** The button the bar belongs to: its opener while open, the one focus returns to once shut. */
export function askBarOwnerId(): string {
  return bar.about ? bar.ownerId : bar.returnFocusTo;
}

export function closeBar(): void {
  if (!bar.about) return;
  bar.returnFocusTo = bar.ownerId;
  bar.about = null;
  bar.ownerId = "";
  bar.text = "";
  bar.validationError = "";
  bar.busy = false;
  notifyAskListeners();
}

function queueAsk(box: HTMLInputElement): void {
  const about = bar.about;
  if (!about || bar.busy) return;
  const text = box.value.trim();
  bar.text = box.value;
  if (!text) bar.validationError = "write a question for chat first";
  else if (text.length > ASK_MAX) bar.validationError = "a question is at most " + ASK_MAX + " characters; this one is " + text.length;
  else bar.validationError = "";
  if (bar.validationError) {
    fieldError(box, bar.validationError);
    return;
  }
  bar.busy = true;
  send<AskRow & ApiError>("POST", ASKS_PATH, { text: text, about: about })
    .then(function (row) {
      box.defaultValue = box.value;
      closeBar();
      refetchAsks();
      copyText(row.copy).then(
        function () {
          notify("queued as " + row.id + " · copied: paste it into chat");
        },
        function () {
          notify("queued as " + row.id + " · not copied: the browser refused; copy it from the asks card");
        }
      );
    })
    .catch(function (reason) {
      bar.busy = false;
      fail("the ask was not queued: " + friendlyError(reason));
      notifyAskListeners();
    });
}

export function renderAskBar(parent: HTMLElement): void {
  const about = bar.about;
  if (!about) return;
  const box = make("section", "ask-bar");
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", WORDS.askChat);
  const what = make("span", "ask-what", WORDS.askChat + " about ");
  what.appendChild(make("b", "", askLabel(about)));
  box.appendChild(what);
  const line = make("div", "ask-line");
  const input = make("input", "dash-name ask-text");
  input.type = "text";
  input.value = bar.text;
  input.defaultValue = "";
  input.placeholder = "your question, up to " + ASK_MAX + " characters";
  input.setAttribute("data-ctl", TEXT_CTL);
  input.setAttribute("aria-label", "question for chat");
  input.oninput = function () {
    bar.text = input.value;
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      queueAsk(input);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      input.defaultValue = input.value;
      closeBar();
    }
  };
  line.appendChild(input);
  line.appendChild(
    button(
      bar.busy ? "queueing…" : "queue for chat",
      function () {
        queueAsk(input);
      },
      { title: "queue it as ask:N and copy that for chat", disabled: bar.busy }
    )
  );
  line.appendChild(
    button(
      "cancel",
      function () {
        input.defaultValue = input.value;
        closeBar();
      },
      { title: "close without asking (Escape)" }
    )
  );
  box.appendChild(line);
  parent.appendChild(box);
  if (bar.validationError) fieldError(input, bar.validationError);
}

/** After a redraw: focus the bar's input once it opens, or its opener once it shuts. */
export function settleAskFocus(root: HTMLElement): void {
  if (bar.about && bar.focusInputOnRender) {
    const input = root.querySelector<HTMLInputElement>('[data-ctl="' + TEXT_CTL + '"]');
    if (input) {
      bar.focusInputOnRender = false;
      input.focus();
    }
    return;
  }
  if (!bar.returnFocusTo) return;
  const opener = root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(bar.returnFocusTo) + '"]');
  bar.returnFocusTo = "";
  if (!opener) return;
  opener.focus({ preventScroll: true });
  opener.scrollIntoView({ block: "center" });
}

export function dropAsk(ask: AskRow): void {
  store.deleteOnce(ask.n, function () {
    return send<AskDropped>("DELETE", ASK_PATH, { rev: ask.rev }, String(ask.n))
      .then(function () {
        notify("deleted " + ask.id);
        refetchAsks();
      })
      .catch(function (reason) {
        const current = store.recoverFromConflict(reason);
        if (current) {
          const body = (reason as StatusError).body as AskStaleResponse;
          fail(body.error || ask.id + " changed since you read it");
        } else fail("could not delete " + ask.id + ": " + friendlyError(reason));
      });
  });
}
