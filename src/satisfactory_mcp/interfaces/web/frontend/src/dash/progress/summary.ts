/* Progress in one line for other surfaces: the old Overview tile and the status strip. */

import { tile } from "../../kit/dashkit";
import { phaseText } from "../../kit/format";
import { hashFor } from "../../map/map";
import { milestones, visible } from "./feeds";

function affordable(): number | null {
  const data = milestones.data;
  if (!data) return null;
  return visible(data.milestones).filter((m) => m.status === "READY" && m.opens_at === null).length;
}

export function readyMilestones(): number | null {
  return affordable();
}

export function milestoneTile(): HTMLElement {
  const data = milestones.data;
  if (!data) return tile("milestones", "–", milestones.failed ? "could not be read" : "loading…", false, hashFor("progress"));
  const top = data.highest_complete_tier;
  return tile(
    "milestones",
    top === null ? "no tier complete" : "tier " + top + " complete",
    (phaseText(data.game_phase) || "no phase in this save") + " · " + affordable() + " affordable",
    false,
    hashFor("progress/buy")
  );
}
