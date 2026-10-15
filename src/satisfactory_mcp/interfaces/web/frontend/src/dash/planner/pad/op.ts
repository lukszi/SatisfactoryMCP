/* The one `site` op a drop pushes (docs/planner-p5_contract.md §3 F6): a move, a turn and a
 * resize all write this, so each is one version and Ctrl+Z undoes it. */

import type { Pad } from "./geometry";

export interface SiteOp {
  op: "site";
  value: Record<string, unknown>;
}

/** `source` is the footprint's: `given` once it was typed, fitted or resized. */
export function siteOp(p: Pad, source: string, label: string): SiteOp {
  return {
    op: "site",
    value: {
      schema: 1,
      origin_m: [p.x_m, p.y_m, null],
      yaw_deg: p.yaw_deg,
      footprint_m: [p.width_m, p.depth_m],
      footprint_source: source,
      origin_label: label,
      when: "",
    },
  };
}
