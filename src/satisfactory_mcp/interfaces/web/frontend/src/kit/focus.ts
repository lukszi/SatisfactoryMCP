/* Keeping keyboard focus through a redraw: views rebuild their DOM on every data change, and a
 * control that vanishes under the cursor would drop focus to the page body. */

var FOCUSABLE = "a[href],button,input,select,textarea,summary,[tabindex]";

function focusKey(node: Element): string {
  var named = node.getAttribute("data-candidate") || node.getAttribute("data-ctl") || node.getAttribute("aria-label") || node.textContent || "";
  return node.tagName + "|" + named.replace(/[▲▼]/g, "").trim();
}

function focusables(container: HTMLElement, key: string): HTMLElement[] {
  return Array.prototype.filter.call(container.querySelectorAll<HTMLElement>(FOCUSABLE), function (node: Element) {
    return focusKey(node) === key;
  }) as HTMLElement[];
}

var rebuilds = 0;

export function isRebuilding(): boolean {
  return rebuilds > 0;
}

function quietly(rebuild: () => void): void {
  rebuilds += 1;
  try {
    rebuild();
  } finally {
    rebuilds -= 1;
  }
}

/* Finds the focused control again by what it is called, not by node identity, because the
 * rebuild replaced the node. */
export function keepFocus(container: HTMLElement, rebuild: () => void): void {
  var was = document.activeElement as HTMLElement | null;
  if (!was || was === container || !container.contains(was)) {
    rebuild();
    return;
  }
  var key = focusKey(was);
  var nth = focusables(container, key).indexOf(was);
  quietly(rebuild);
  var now = document.activeElement;
  if (was.isConnected || (now && now !== document.body && container.contains(now))) return;
  var same = focusables(container, key);
  var again = same[nth] || same[0] || container.querySelector<HTMLElement>("h1");
  if (!again) return;
  if (again.tagName === "H1") again.tabIndex = -1;
  again.focus({ preventScroll: true });
}
