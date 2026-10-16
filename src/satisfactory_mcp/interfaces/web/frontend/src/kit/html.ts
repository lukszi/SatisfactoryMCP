/* The escaping markup builder every new page and kit part renders with: `html` is a tagged
 * template whose holes are escaped unless they are already Safe. See docs/frontend_kit.md. */

import { esc } from "./dom";

export interface Safe {
  readonly safeHtml: string;
}

export type Hole = Safe | string | number | boolean | null | undefined | Hole[];

export function isSafe(value: unknown): value is Safe {
  return typeof value === "object" && value !== null && typeof (value as Safe).safeHtml === "string";
}

/** Markup the caller vouches for; never pass data through this. */
export function trusted(markup: string): Safe {
  return { safeHtml: markup };
}

/** null, undefined and false render nothing, so `cond && html\`…\`` works in a hole. */
export function hole(value: Hole): string {
  if (value === null || value === undefined || value === false) return "";
  if (Array.isArray(value)) return value.map(hole).join("");
  if (isSafe(value)) return value.safeHtml;
  return esc(value);
}

export function html(strings: TemplateStringsArray, ...holes: Hole[]): Safe {
  let out = strings[0] || "";
  for (let i = 0; i < holes.length; i++) out += hole(holes[i]!) + (strings[i + 1] || "");
  return trusted(out);
}

export function join(parts: Hole[], separator?: Safe): Safe {
  return trusted(
    parts
      .map(hole)
      .filter(function (part) {
        return part !== "";
      })
      .join(separator ? separator.safeHtml : "")
  );
}

/** Space-joined class names, skipping the falsy ones. */
export function cls(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

/** Replaces an element's children with the markup. */
export function mount(target: Element, markup: Safe): void {
  target.innerHTML = markup.safeHtml;
}

/** One element out of markup that has exactly one root. */
export function toElement<T extends Element = HTMLElement>(markup: Safe): T {
  const box = document.createElement("template");
  box.innerHTML = markup.safeHtml.trim();
  return box.content.firstElementChild as T;
}

/** The text with every case-insensitive occurrence of `query` wrapped in <mark>; all escaped. */
export function marked(text: string, query: string): Safe {
  const q = query.trim().toLowerCase();
  if (!q) return trusted(esc(text));
  const lower = text.toLowerCase();
  let out = "";
  let at = 0;
  let hit = lower.indexOf(q, at);
  while (hit >= 0) {
    out += esc(text.slice(at, hit)) + "<mark>" + esc(text.slice(hit, hit + q.length)) + "</mark>";
    at = hit + q.length;
    hit = lower.indexOf(q, at);
  }
  return trusted(out + esc(text.slice(at)));
}
