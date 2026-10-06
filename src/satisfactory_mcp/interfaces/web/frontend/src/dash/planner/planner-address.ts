/* The planner's addresses: `planner/<key>` and what may follow it. */

export function trackDash(key: string, stage: number): string {
  return "planner/" + key + "/track" + (stage ? "/" + stage : "");
}
