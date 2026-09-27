/* The dashboard address: reading `tab/subject` apart, and going to one.
 * One parser, so every tab splits its address the same way. */

import { hashFor } from "./map";
import { state } from "./state";

export interface DashParts {
  tab: string;
  subject: string;
  rest: string[];
}

export function dashParts(dash?: string): DashParts {
  var raw = dash === undefined ? state.dash : dash;
  var cut = raw.indexOf("/");
  var subject = cut < 0 ? "" : raw.slice(cut + 1);
  return {
    tab: cut < 0 ? raw : raw.slice(0, cut),
    subject: subject,
    rest: subject ? subject.split("/") : [],
  };
}

export function go(dash: string, replace?: boolean): void {
  if (replace) location.replace(hashFor(dash));
  else location.hash = hashFor(dash);
}
