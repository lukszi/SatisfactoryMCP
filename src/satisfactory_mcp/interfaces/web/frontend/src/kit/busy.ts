/* The loading timing rule (docs/frontend_kit.md): aria-busy at once, `.is-busy` (spinner,
 * hairline) only after 300 ms, and once shown it stays at least 500 ms. */

export const BUSY_DELAY_MS = 300;
export const BUSY_MIN_MS = 500;
export const ELAPSED_AFTER_MS = 1000;

export interface BusyTarget {
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
  classList: { add(name: string): void; remove(name: string): void };
}

/** Marks `target` busy while `work` runs; resolves with the work's result after the indicator
 *  has had its minimum time, rejects the same way. */
export function busy<T>(target: BusyTarget, work: Promise<T>): Promise<T> {
  let shownAt = 0;
  target.setAttribute("aria-busy", "true");
  const showTimer = setTimeout(function () {
    shownAt = Date.now();
    target.classList.add("is-busy");
  }, BUSY_DELAY_MS);
  const settle = function (): Promise<void> {
    clearTimeout(showTimer);
    const wait = shownAt ? Math.max(0, shownAt + BUSY_MIN_MS - Date.now()) : 0;
    return new Promise(function (resolve) {
      setTimeout(function () {
        target.classList.remove("is-busy");
        target.removeAttribute("aria-busy");
        resolve();
      }, wait);
    });
  };
  return work.then(
    function (value) {
      return settle().then(function () {
        return value;
      });
    },
    function (reason: unknown) {
      return settle().then(function () {
        throw reason;
      });
    }
  );
}

/** An async button: disabled at once, label kept, spinner after 300 ms, "… 1.2 s" after 1 s. */
export function busyButton<T>(button: HTMLButtonElement, work: () => Promise<T>): Promise<T> {
  const started = Date.now();
  let elapsed: HTMLElement | null = null;
  button.disabled = true;
  if (!button.querySelector(".spinner")) button.insertAdjacentHTML("afterbegin", '<span class="spinner" aria-hidden="true"></span>');
  const ticker = setInterval(function () {
    const ms = Date.now() - started;
    if (ms < ELAPSED_AFTER_MS) return;
    if (!elapsed) {
      elapsed = document.createElement("span");
      elapsed.className = "elapsed";
      elapsed.setAttribute("role", "status");
      button.after(elapsed);
    }
    elapsed.textContent = "… " + (ms / 1000).toFixed(1) + " s";
  }, 100);
  const done = function (): void {
    clearInterval(ticker);
    if (elapsed) elapsed.remove();
    button.disabled = false;
  };
  return busy(button, work()).then(
    function (value) {
      done();
      return value;
    },
    function (reason: unknown) {
      done();
      throw reason;
    }
  );
}
