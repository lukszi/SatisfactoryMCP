/* Text widths for laying the graph out before anything is drawn, and the SVG element helper. */

const SVG_NS = "http://www.w3.org/2000/svg";

export const SMALL_FONT = "11px";
export const TITLE_FONT = "600 12px";
export const LINE = 14;

let ruler: CanvasRenderingContext2D | null = null;
let family = "";

export function measure(text: string, font: string): number {
  if (typeof document === "undefined") return text.length * 6.5;
  if (!family) family = getComputedStyle(document.body).fontFamily || "sans-serif";
  if (!ruler) ruler = document.createElement("canvas").getContext("2d");
  if (!ruler) return text.length * 6.5;
  ruler.font = font + " " + family;
  return Math.ceil(ruler.measureText(text).width);
}

export function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
  Object.keys(attrs).forEach(function (name) {
    node.setAttribute(name, String(attrs[name]));
  });
  return node;
}

export function svgText(className: string, x: number, y: number, words: string): SVGTextElement {
  const t = svg("text", { class: className, x: x, y: y });
  t.textContent = words;
  return t;
}
