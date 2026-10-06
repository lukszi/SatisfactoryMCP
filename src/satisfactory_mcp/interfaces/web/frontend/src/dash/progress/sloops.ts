/* Progress > Somersloops: sloops free and slotted, and every amplified machine. */

import { appendNote, heading, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber } from "../../kit/format";
import { settingOn } from "../../app/settings";
import { amountList, amountsByPlace } from "./cells";
import { sloops, waiting } from "./feeds";

import type { Column } from "../../kit/dashkit";
import type { SloopsResponse } from "../../api/shapes";
import type { PointButton } from "./shards";

type Amplified = SloopsResponse["holders"][number];

function boostDisagrees(holder: Amplified): boolean {
  return holder.boost !== null && holder.boost_in_save !== null && Math.abs(holder.boost - holder.boost_in_save) > 1e-6;
}

function sloopColumns(point: PointButton): Column<Amplified>[] {
  return [
    {
      key: "name",
      label: "building",
      render: function (holder) {
        return holder.name;
      },
    },
    {
      key: "sloops",
      label: "somersloops",
      align: "right",
      render: function (holder) {
        return formatNumber(holder.sloops);
      },
    },
    {
      key: "boost",
      label: "boost",
      align: "right",
      title: "what the plan model says the slots are worth",
      render: function (holder) {
        return holder.boost === null ? "–" : formatNumber(holder.boost, 2) + "×";
      },
    },
    {
      key: "saved",
      label: "boost in save",
      align: "right",
      title: "the multiplier the save carries",
      render: function (holder) {
        return holder.boost_in_save === null ? "–" : formatNumber(holder.boost_in_save, 2) + "×";
      },
      tone: function (holder) {
        return boostDisagrees(holder) ? "bad" : "";
      },
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (holder) {
        return point(holder);
      },
    },
  ];
}

export function renderSloops(body: HTMLElement, point: PointButton): void {
  const data = sloops.data;
  if (!data) {
    waiting(body, sloops);
    return;
  }
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", formatNumber(data.free), data.by_place.length ? amountsByPlace(data.by_place) : "none in stock"));
  tiles.appendChild(tile("slotted", data.measured ? formatNumber(data.committed) : "–", data.measured ? "in production machines" : "this save does not record slots"));
  tiles.appendChild(tile("owned", formatNumber(data.owned), "free plus slotted"));
  if (data.mercer_spheres || settingOn("spoilers")) tiles.appendChild(tile("mercer spheres", formatNumber(data.mercer_spheres), "counted apart, never added in"));
  body.appendChild(tiles);
  if (!data.amplifier_researched && data.amplifier_spoiler && !settingOn("spoilers")) {
    appendNote(body, "no somersloop can go into a machine yet: the research for it is still locked");
  } else if (!data.amplifier_researched) {
    appendNote(
      body,
      (data.amplifier_research || "Production Amplifier") +
        " is not researched, so no somersloop can go into a machine yet. MAM cost: " +
        amountList(data.amplifier_cost)
    );
  }
  const card = make("section", "dash-card");
  heading(card, "amplified machines (" + data.holders.length + ")");
  if (!data.holders.length) appendNote(card, "no machine holds a somersloop");
  else {
    card.appendChild(table(sloopColumns(point), data.holders, { caption: "amplified machines" }));
    const off = data.holders.filter(boostDisagrees).length;
    if (off) appendNote(card, "boost and boost in save disagree on " + off + " machines");
  }
  body.appendChild(card);
}
