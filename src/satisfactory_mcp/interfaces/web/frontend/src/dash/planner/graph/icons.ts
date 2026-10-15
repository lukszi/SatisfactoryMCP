/* Item pictures for the graph's nodes, from `/api/icons/` when this install generated them.
 * One probe per page decides; until it answers, nodes draw the neutral glyph. */

let served: boolean | null = null;

export function iconUrl(itemId: string | null, redraw: () => void): string | null {
  if (served === null && typeof fetch !== "undefined") {
    served = false;
    fetch("/api/icons/Desc_IronPlate_C", { method: "HEAD" })
      .then(function (response) {
        served = response.status === 200;
        if (served) redraw();
      })
      .catch(function () {});
  }
  if (!served || !itemId || itemId === "MW" || itemId === "power") return null;
  return "/api/icons/" + encodeURIComponent(itemId);
}
