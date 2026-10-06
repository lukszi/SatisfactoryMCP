/* Asks: questions queued on the page for chat as ask:N, the one ask bar, and the store.
 * See docs/planner-p4_contract.md §2 F6 and §4. */

import { send } from "../api/client";
import { copyText } from "../kit/copy";
import { button, fieldError } from "../kit/dashkit";
import { make } from "../kit/dom";
import { liveStore } from "./livestore";
import { state } from "../app/state";
import { fail, friendly, note } from "../kit/toast";
import { ASK_KIND, W } from "../kit/words";

import type { ApiError, ApiPath, StatusError } from "../api/client";
import type { AskAbout, AskDropped, AskRow, AsksResponse, AskStaleResponse } from "../api/shapes";

export var ASK_MAX = 200;

var LABEL_MAX = 120;
var REF_MAX = 200;
var ASKS: ApiPath = "/api/asks";
var ASK_ONE: ApiPath = "/api/asks/{n}";
var TEXT_CTL = "ask-text";

var store = liveStore<AsksResponse, AskRow>(ASKS, function (data) {
  return data.asks;
}, "ask");
var bar = { about: null as AskAbout | null, opener: "", text: "", problem: "", enter: false, back: "", busy: false };

export var onAsks = store.on;
export var askStore = store.read;
export var refetchAsks = store.refetch;
export var loadAsks = store.load;
var notify = store.notify;

export function liveAsks(): AskRow[] {
  var data = askStore().data;
  return data ? data.asks : [];
}

export function asksFor(planKey: string): AskRow[] {
  return liveAsks().filter(function (a) {
    return a.about.plan === planKey;
  });
}

export function askMarks(planKey: string, kind: string, ref: string): AskRow[] {
  return asksFor(planKey).filter(function (a) {
    return a.about.kind === kind && a.about.ref === ref && a.state !== "answered";
  });
}

export function onActivity(entry: { world: string; kind: string }): void {
  if (entry.world === state.world && entry.kind.indexOf("ask.") === 0) refetchAsks();
}

function clip(text: string, max: number): string {
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

export function askLabel(about: AskAbout): string {
  return (ASK_KIND[about.kind] || about.kind) + " “" + about.label + "”";
}

export function askButton(about: AskAbout, ctl: string, text?: string): HTMLButtonElement {
  var subject: AskAbout = { kind: about.kind, label: clip(about.label, LABEL_MAX), ref: clip(about.ref, REF_MAX) };
  if (about.plan) subject.plan = about.plan;
  if (about.rev) subject.rev = about.rev;
  var id = "ask:" + ctl;
  var b = button(
    text || "ask",
    function () {
      openBar(subject, id);
    },
    { title: "queue a question for chat about this " + (ASK_KIND[about.kind] || about.kind), label: "ask chat about " + askLabel(subject) }
  );
  b.setAttribute("data-ctl", id);
  b.setAttribute("aria-expanded", String(bar.opener === id && !!bar.about));
  return b;
}

function openBar(about: AskAbout, opener: string): void {
  bar.about = about;
  bar.opener = opener;
  bar.problem = "";
  bar.enter = true;
  bar.back = "";
  notify();
}

export function askOpen(): boolean {
  return !!bar.about;
}

export function askOpener(): string {
  return bar.about ? bar.opener : bar.back;
}

export function closeBar(): void {
  if (!bar.about) return;
  bar.back = bar.opener;
  bar.about = null;
  bar.opener = "";
  bar.text = "";
  bar.problem = "";
  bar.busy = false;
  notify();
}

function queue(box: HTMLInputElement): void {
  var about = bar.about;
  if (!about || bar.busy) return;
  var text = box.value.trim();
  bar.text = box.value;
  if (!text) bar.problem = "write a question for chat first";
  else if (text.length > ASK_MAX) bar.problem = "a question is at most " + ASK_MAX + " characters; this one is " + text.length;
  else bar.problem = "";
  if (bar.problem) {
    fieldError(box, bar.problem);
    return;
  }
  bar.busy = true;
  send<AskRow & ApiError>("POST", ASKS, { text: text, about: about })
    .then(function (row) {
      box.defaultValue = box.value;
      closeBar();
      refetchAsks();
      copyText(row.copy).then(
        function () {
          note("queued as " + row.id + " · copied: paste it into chat");
        },
        function () {
          note("queued as " + row.id + " · not copied: the browser refused; copy it from the asks card");
        }
      );
    })
    .catch(function (reason) {
      bar.busy = false;
      fail("the ask was not queued: " + friendly(reason));
      notify();
    });
}

export function renderAskBar(parent: HTMLElement): void {
  var about = bar.about;
  if (!about) return;
  var box = make("section", "ask-bar");
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", W.askChat);
  var what = make("span", "ask-what", W.askChat + " about ");
  what.appendChild(make("b", "", askLabel(about)));
  box.appendChild(what);
  var line = make("div", "ask-line");
  var input = make("input", "dash-name ask-text");
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
      queue(input);
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
        queue(input);
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
  if (bar.problem) fieldError(input, bar.problem);
}

export function settleAskFocus(root: HTMLElement): void {
  if (bar.about && bar.enter) {
    var input = root.querySelector<HTMLInputElement>('[data-ctl="' + TEXT_CTL + '"]');
    if (input) {
      bar.enter = false;
      input.focus();
    }
    return;
  }
  if (!bar.back) return;
  var opener = root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(bar.back) + '"]');
  bar.back = "";
  if (!opener) return;
  opener.focus({ preventScroll: true });
  opener.scrollIntoView({ block: "center" });
}

export function dropAsk(row: AskRow): void {
  store.once(row.n, function () {
    return send<AskDropped>("DELETE", ASK_ONE, { rev: row.rev }, String(row.n))
      .then(function () {
        note("deleted " + row.id);
        refetchAsks();
      })
      .catch(function (reason) {
        var current = store.refused(reason);
        if (current) {
          var body = (reason as StatusError).body as AskStaleResponse;
          fail(body.error || row.id + " changed since you read it");
        } else fail("could not delete " + row.id + ": " + friendly(reason));
      });
  });
}
