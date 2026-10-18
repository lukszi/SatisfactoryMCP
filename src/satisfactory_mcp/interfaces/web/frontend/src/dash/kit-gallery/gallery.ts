/* The kit gallery, `#kit`: every kit part in its states with sample data, for screenshot
 * checks. Not linked from the page. */

import "./gallery.css";
import { busy, busyButton } from "../../kit/busy";
import { html, mount, type Safe } from "../../kit/html";
import { toast } from "../../kit/overlay/panels";
import { wireMoreMenus } from "../../kit/overlay/menu";
import { btn, busyBtn, check, filterChip, filterField, glyphBtn, moreBtn, seg, sortSelect, switchBox } from "../../kit/ui/controls";
import { acts, actSlots, chevron, chips, entry, expandBody, facts, group, identity, list, metric, nameLine, wireExpand } from "../../kit/ui/entry";
import { fadd, fnum, form, frange, frow, ftext } from "../../kit/ui/forms";
import { bar, chip, flow, jobBar, mixBar, pill, slotTile, tagChip } from "../../kit/ui/marks";
import { sidePanel } from "../../kit/overlay/panels";
import { blockHead, dashH, emptyLine, fig, figSub, icon, note, toolrow } from "../../kit/ui/text";
import { openSettings } from "../../app/settings-dialog/dialog";

const PLATE = "Desc_IronPlate_C";
const INGOT = "Desc_IronIngot_C";
const SCREW = "Desc_IronScrew_C";

function section(title: string, inner: Safe): Safe {
  return html`<section class="kit-sec"><h2 class="dash-h">${title}</h2>${inner}</section>`;
}

function headings(): Safe {
  return section(
    "headings and figures",
    html`${blockHead("power", html`${fig("+124", "MW headroom now")}${fig("62", "% of 340 MW", "bad", 62)}${figSub("41 unmeasured, charged in full")}`)}
    ${toolrow(dashH("need action", 3), html`${filterField("kit-filter", "filter", "")}${sortSelect("kit-sort", [["need", "need action"], ["name", "name"]], "need")}`)}
    <div class="figs">${fig("48", "of 52 machines", undefined, 92)}${fig("3", "need action", "bad")}${fig("1", "blocked", "blocked")}</div>
    ${note("one line under the list it describes")}${emptyLine("no factories yet", "name a cluster on the Factories page")}`
  );
}

function stackedList(): Safe {
  const smelter = entry(
    { key: "kit-smelt", open: true },
    html`${icon(INGOT, "Iron Ingot")}${identity("Iron Ingot", "Smelter · 8 machines")}${metric("8", "machines")}${facts([["100%", "clock"], ["32", "MW"]])}${chevron("kit-smelt", "Iron Ingot", true)}${expandBody(html`<p class="note">every machine at 100% clock</p>`)}`
  );
  const rows = [
    entry({ href: "#kit" }, html`${icon(PLATE, "Iron Plate")}${identity("Iron Plate", "Constructor", "#kit")}${metric("12", "machines")}${facts([["240", "/min"], ["48", "MW"]])}${chips(chip("ok", "ok"))}`),
    entry({ tone: "bad" }, html`${icon(SCREW, "Screw")}${identity("Screw", "Constructor")}${metric("3", "starved", "bad")}${facts([["0", "/min"], ["12", "MW"]])}${chips(chip("3 starved", "bad"))}`),
    entry({ tone: "blocked" }, html`${icon(null, "Reinforced Iron Plate")}${identity("Reinforced Iron Plate", "Assembler")}${metric("1", "blocked", "blocked")}${facts([["5", "/min"], ["15", "MW"]])}${chips(chip("1 blocked", "blocked"))}`),
    entry({ tone: "mid", focusable: true }, html`${icon(null, "Rotor")}${identity("Rotor", "Assembler · paused")}${metric("2", "machines")}${facts([["–", "/min"], ["0", "MW"]])}${chips(chip("paused", "mid"))}`),
    smelter,
  ];
  return html`${dashH("stacked list · compact", 5, "h3")}${list("kit", "stacked", html`${group("need action", 2)}${rows.slice(1, 3)}${group("running", 3)}${rows[0]}${rows[3]}${rows[4]}`)}`;
}

function cards(): Safe {
  const card = function (name: string, tone: "" | "bad", n: string, pct: number): Safe {
    return entry(
      { tone: tone, href: "#kit" },
      html`${icon(PLATE, name)}${identity(name, "48 machines · 87% uptime", "#kit")}${metric(n, "need action", tone ? "bad" : "none")}${acts(html`${glyphBtn("◎", "show " + name + " on the map")}${moreBtn(name, ["rename", "plan from factory"])}`)}<div class="full">${mixBar([["ok", pct], ["blocked", 5], ["bad", 100 - pct - 5]], "machines by state")}</div>`
    );
  };
  return html`${dashH("cards · comfortable", 3, "h3")}${list("kitcard", "grid", [card("Iron Works North", "", "none", 95), card("Steel Valley", "bad", "3", 70), card("Rotor Hill", "", "none", 90)])}`;
}

/* Every row's buttons at one x: one slot, the split slot, a chip in the slot, ◎ leading. */
function slotList(): Safe {
  const rows = [
    entry({}, html`${identity("Terrain · 15 Oct", "done · 1 h 42 min")}${actSlots(btn("make default"), { pin: "", more: moreBtn("Terrain", ["make default and delete"]) })}`),
    entry({}, html`${identity("Satellite · 18 Oct", "46% · 32 min left")}${actSlots({ split: [btn("log"), btn("cancel")] }, { pin: "" })}`),
    entry({}, html`${identity("paint layers", "queued")}${actSlots(chip("queued", "muted"), { pin: "" })}`),
    entry({}, html`${identity("Iron Works North", "pinned on the map")}${actSlots(btn("rename"), { pin: glyphBtn("◎", "show on the map"), more: moreBtn("Iron Works North", ["delete"]) })}`),
  ];
  return html`${dashH("action slots", rows.length, "h3")}${list("kitslots", "stacked", rows, "nolead")}`;
}

function marks(): Safe {
  const tones = html`${chip("plain")}${chip("done", "ok")}${chip("3 starved", "bad")}${chip("1 blocked", "blocked")}${chip("paused", "mid")}${chip("locked", "muted")}${chip("changed", "accent")}${chip("pin:4", "id")}${chip("removing", "remove")}${tagChip("alt")}`;
  const pills = flow([{ name: "Iron Ingot", icon: INGOT, rate: "30" }], [{ name: "Iron Plate", icon: PLATE, rate: "20 /min" }, { name: "Screw", icon: SCREW, rate: "5 /min" }]);
  const bars = html`<div class="kit-bars">${bar(72, "progress", "built 72%")}${bar(45, "util", "clock 45%")}${mixBar([["ok", 40], ["mid", 6], ["blocked", 1], ["bad", 3]], "machines by state")}${jobBar(46, "rendering")}${jobBar(null, "a stage with no measure")}</div>`;
  const slots = html`<div class="slots">${slotTile({ name: "Iron Plate", icon: PLATE, value: "94k" })}${slotTile({ name: "Screw", icon: SCREW, value: "3.9k", on: true })}${slotTile({ name: "Iron Ingot", icon: INGOT, locked: true })}${slotTile({ name: "Storage Container", value: "12", unit: "of 24", fill: 50 })}</div>`;
  return section("chips, pills, bars, slots", html`<div class="kit-row">${tones}</div><div class="kit-row">${pill({ name: "Iron Plate", icon: PLATE, rate: "20 /min", href: "#kit" })}${pills}</div>${bars}${slots}`);
}

function controls(): Safe {
  const segItems = [
    { value: "held", label: "held" },
    { value: "all", label: "all" },
    { value: "containers", label: "containers" },
  ];
  return section(
    "controls",
    html`<div class="kit-row">${seg("kit-seg", segItems, "held", "items view")}${filterChip("kit-fc1", "nodes", true)}${filterChip("kit-fc2", "pickups", false)}${switchBox("kit-sw1", true, "on")}${switchBox("kit-sw2", false, "off")}${check("kit-ck", true, "live sun")}</div>
    <div class="kit-row">${btn("action")}${btn("disabled", { disabled: true })}${btn("pressed", { extra: "on" })}${busyBtn("regenerate", { id: "kit-busy" })}${glyphBtn("◎", "map pin")}${glyphBtn("×", "close")}${moreBtn("sample", ["rename", "change icon", "delete"])}${btn("toast", { id: "kit-toast" })}${btn("settings dialog", { id: "kit-settings" })}</div>`
  );
}

function forms(): Safe {
  const rows = html`${frow("item", ftext("kit-item", "Iron Plate"), { forId: "kit-item" })}${frow("rate", fnum("kit-rate", 120, "/min", { min: 0 }), { forId: "kit-rate" })}${frow("goal", seg("kit-goal", [{ value: "min", label: "min machines" }, { value: "raw", label: "least raw" }], "raw", "goal"))}${frow("size", frange("kit-size", 5, 2, "4096 px"))}${frow("banned recipes", fadd("kit-ban", "add a recipe", "ban a recipe", ["Cast Screw", "Iron Wire"]))}${frow("", html`<span class="btn-row">${busyBtn("create")}</span><p class="note">≈ 24 min · keeps 410 MB</p>`)}`;
  return section("form rows", html`<div class="card">${form(rows)}</div>`);
}

function loading(): Safe {
  return section(
    "loading and panels",
    html`<div class="kit-split"><div><div class="loadframe is-busy" role="status"><span class="lf-in"><span class="spinner" aria-hidden="true"></span>loading “Iron Works North”</span></div>${list("kit", "stacked", entry({}, html`${icon(PLATE, "Iron Plate")}${identity("refreshing list", "the old rows stay")}${metric("12")}`), "is-busy")}</div>
    ${sidePanel(html`${icon(PLATE, "Iron Plate")}<h2 class="dash-h">Iron Plate</h2>${chip("solid")}`, html`${dashH("made by", 2, "h3")}${list("kit", "stacked", entry({}, html`${icon(null, "Constructor")}${nameLine("Iron Plate", "Constructor · 4 MW")}`))}`)}</div>`
  );
}

function wireGallery(root: HTMLElement): void {
  wireExpand(root);
  wireMoreMenus(root, function (label) {
    toast("“" + label + "” picked");
  });
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    if (target.closest("#kit-toast")) toast("a toast line");
    if (target.closest("#kit-settings")) openSettings("");
    const busyTarget = target.closest<HTMLButtonElement>("#kit-busy");
    if (busyTarget) busyButton(busyTarget, () => new Promise((resolve) => setTimeout(resolve, 1800)));
  });
  const frame = root.querySelector<HTMLElement>(".loadframe");
  if (frame) busy(frame, new Promise(() => undefined));
}

export function renderKitGallery(parent: HTMLElement): void {
  const root = document.createElement("div");
  root.className = "kit-gallery";
  mount(root, html`<h1>kit</h1><p class="note">every part of the design system in its states</p>${headings()}${section("lists and entries", html`${stackedList()}${cards()}${slotList()}`)}${marks()}${controls()}${forms()}${loading()}`);
  parent.appendChild(root);
  wireGallery(root);
}
