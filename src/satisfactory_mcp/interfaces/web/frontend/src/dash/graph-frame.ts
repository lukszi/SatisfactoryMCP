/* The frame a production graph is shown in: zoomed with ctrl+scroll, panned by dragging, fitted
 * on a double click. See docs/frontend_vision.md §9.8. */

import { make } from "../kit/dom";
import { fitScale, ROOM_H, ZOOM } from "./frame-fit";

import type { FrameOptions, FrameView } from "./frame-fit";

export const GRAPH_HINT = "ctrl+scroll zooms, drag pans, double-click fits";

export function zoomableFrame(root: SVGSVGElement, width: number, height: number, options?: FrameOptions): HTMLElement {
  const box = make("div", "graph-frame");
  box.appendChild(root);
  const fitAll = !!options?.fitAll;
  const view: FrameView = options?.view || { zoom: 1 };
  let base = 1;
  let fitted = false;
  let zoom = view.zoom;
  const edge = function () {
    box.classList.toggle("more", box.scrollLeft + box.clientWidth < box.scrollWidth - 2);
  };
  const size = function () {
    const scale = base * zoom;
    root.style.width = Math.round(width * scale) + "px";
    root.style.height = Math.round(height * scale) + "px";
    edge();
  };
  const fit = function () {
    const room = box.clientWidth;
    if (!room) return;
    base = fitScale(room, Math.round(window.innerHeight * ROOM_H), width, height, fitAll);
    fitted = true;
    size();
  };
  size();
  box.addEventListener("scroll", edge);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(function () {
      if (zoom === 1 || !fitted) fit();
    }).observe(box);
  }
  box.addEventListener(
    "wheel",
    function (event) {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = box.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const old = base * zoom;
      const next = Math.min(ZOOM[1]!, Math.max(ZOOM[0]!, old * (event.deltaY > 0 ? 1 / 1.15 : 1.15)));
      zoom = next / base;
      view.zoom = zoom;
      size();
      box.scrollLeft = ((box.scrollLeft + px) * next) / old - px;
      box.scrollTop = ((box.scrollTop + py) * next) / old - py;
    },
    { passive: false }
  );
  let drag: { x: number; y: number } | null = null;
  box.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    if ((event.target as Element).closest(".graph-node.pickable")) return;
    drag = { x: event.clientX, y: event.clientY };
    box.setPointerCapture(event.pointerId);
    box.classList.add("dragging");
  });
  box.addEventListener("pointermove", function (event) {
    if (!drag) return;
    box.scrollLeft -= event.clientX - drag.x;
    box.scrollTop -= event.clientY - drag.y;
    drag = { x: event.clientX, y: event.clientY };
  });
  const drop = function () {
    drag = null;
    box.classList.remove("dragging");
  };
  box.addEventListener("pointerup", drop);
  box.addEventListener("pointercancel", drop);
  box.addEventListener("dblclick", function (event) {
    if ((event.target as Element).closest(".graph-node.pickable")) return;
    zoom = 1;
    view.zoom = 1;
    fit();
    box.scrollLeft = 0;
    box.scrollTop = 0;
  });
  return box;
}
