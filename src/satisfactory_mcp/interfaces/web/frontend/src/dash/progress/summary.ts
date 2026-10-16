/* Progress in one figure for the status strip: the milestones affordable now. */

import { milestones, visible } from "./feeds";

export function readyMilestones(): number | null {
  const data = milestones.data;
  if (!data) return null;
  return visible(data.milestones).filter((m) => m.status === "READY" && m.opens_at === null).length;
}
