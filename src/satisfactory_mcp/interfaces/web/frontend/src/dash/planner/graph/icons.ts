/* Pictures for the graph's nodes, from `/api/icons/` when this install generated them: a
 * process shows its building, an item node its item. One probe per page decides; until it
 * answers, nodes draw the neutral glyph and the answer redraws them. */

import type { PlanNode } from "./model";

let served: boolean | null = null;
let build = "";

const NO_PICTURE = ["MW", "power"];

/** The descriptor class a node's picture is filed under, or null for none (power). */
export function iconIdFor(node: Pick<PlanNode, "kind" | "buildingIcon" | "itemId">): string | null {
  const item = node.itemId && NO_PICTURE.indexOf(node.itemId) < 0 ? node.itemId : null;
  return node.kind === "process" ? node.buildingIcon || item : item;
}

export function iconUrl(id: string | null, redraw: () => void): string | null {
  if (served === null && typeof fetch !== "undefined") {
    served = false;
    fetch("/api/icons/Desc_IronPlate_C", { method: "HEAD" })
      .then(function (response) {
        served = response.status === 200;
        build = response.headers.get("X-Icons-Build") || "";
        if (served) redraw();
      })
      .catch(function () {});
  }
  if (!served || !id) return null;
  return "/api/icons/" + encodeURIComponent(id) + (build ? "?v=" + encodeURIComponent(build) : "");
}
