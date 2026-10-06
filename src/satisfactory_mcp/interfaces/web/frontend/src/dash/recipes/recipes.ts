/* The Recipes codex: the dashboard's `dash=recipes…` section. See docs/frontend_vision.md §10. */

import { state } from "../../app/state";
import { detailOf, renderBrowse } from "./browse";
import { probeIcons, setRedraw } from "./cache";
import { renderItem, renderRecipe } from "./detail";

let rendered = "";

export function renderRecipes(body: HTMLElement, subject: string, rerender: () => void): void {
  setRedraw(rerender);
  probeIcons();
  const arrived = rendered !== state.dash;
  rendered = state.dash;
  const detail = detailOf(subject);
  if (detail && detail.kind === "item") renderItem(body, detail.id);
  else if (detail) renderRecipe(body, detail.id);
  else renderBrowse(body, subject, arrived);
}
