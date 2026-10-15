/* How large a graph is drawn in its frame: the pure part of graph-frame.ts. */

const MAX_SCALE = 14 / 12;
export const ZOOM = [0.25, 3];
/** The share of the window's height a fitted graph may take, as the frame's CSS caps it. */
export const ROOM_H = 0.78;

/** The zoom a caller keeps across redraws; 1 means "fitted". */
export interface FrameView {
  zoom: number;
}

export interface FrameOptions {
  /** Shrink to show the whole graph, not just fill the width. */
  fitAll?: boolean;
  view?: FrameView;
}

/** The base scale for content in a room: the whole graph with `fitAll`, else the width only
 *  and never below 1, so a wide graph scrolls. */
export function fitScale(roomW: number, roomH: number, width: number, height: number, fitAll: boolean): number {
  if (!fitAll) return Math.min(MAX_SCALE, Math.max(1, roomW / width));
  const s = Math.min(MAX_SCALE, roomW / Math.max(1, width), roomH > 0 ? roomH / Math.max(1, height) : Infinity);
  return Math.max(ZOOM[0]!, s);
}
