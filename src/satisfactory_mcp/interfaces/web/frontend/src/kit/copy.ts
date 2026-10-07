/* Click a selector to copy it.
 *
 * Every popup on this page ends in one -- `node:BP_ResourceNode26_99`, `label:Coal Power`,
 * an instance id -- and they are the interchange format: the next step after reading a card
 * is naming that thing to an MCP tool. Retyping it by hand was the whole gap.
 *
 * ONE DELEGATED LISTENER, on the document, because there is nothing to bind to when the
 * markup is made: Leaflet holds a bound popup as a STRING and builds its DOM on the click.
 * The event does reach here from inside a card -- Leaflet's disableClickPropagation stops
 * mousedown, dblclick and contextmenu, and never click.
 */

import { COPY_ATTR, COPY_CLASS } from "./dom";
import { fail, notify } from "./toast";

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  /* The async clipboard needs a secure context. `satisfactory-mcp-web` binds 127.0.0.1,
   * which is one, so reaching this branch means the page came through a proxy -- and the
   * deprecated call still works there. */
  const pad = document.createElement("textarea");
  pad.value = text;
  pad.setAttribute("readonly", "");
  pad.style.position = "fixed";
  pad.style.opacity = "0";
  document.body.appendChild(pad);
  pad.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (ignored) {
    copied = false;
  }
  document.body.removeChild(pad);
  if (copied) return Promise.resolve();
  return Promise.reject(new Error("the browser refused to copy"));
}

function copyFrom(event: Event): void {
  const target = event.target as Element | null;
  const span = target && target.closest ? target.closest("." + COPY_CLASS) : null;
  if (!span) return;
  const text = span.getAttribute(COPY_ATTR) || span.textContent || "";
  // Said out loud both ways: a copy that silently did nothing is worse than no affordance,
  // because the reader pastes whatever was in the clipboard before.
  copyText(text).then(
    function () {
      notify("copied " + text);
    },
    function (error) {
      fail("could not copy " + text + ": " + String((error && error.message) || error));
    }
  );
}

export function listenForCopies(): void {
  document.addEventListener("click", copyFrom);
  document.addEventListener("keydown", function (event) {
    const target = event.target as Element | null;
    if (event.key !== "Enter" || !target || target.tagName === "BUTTON" || !target.classList.contains(COPY_CLASS)) return;
    event.preventDefault();
    copyFrom(event);
  });
}
