/* Progress > Power shards: shards free, craftable and slotted, and every overclocked building. */

import { appendNote, heading, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber, pct } from "../../kit/format";
import { amountList, PLACES } from "./cells";
import { shards, waiting } from "./feeds";

import type { Column, SortState } from "../../kit/dashkit";
import type { ShardsResponse } from "../../api/shapes";

interface Placed {
  x_m: number | null;
  y_m: number | null;
}

export type PointButton = (row: Placed) => HTMLElement;

type Holder = ShardsResponse["holders"][number];

type Slug = ShardsResponse["slugs"][number];

const shardSort: SortState = { key: "clock", desc: true };

const SLUG_COLUMNS: Column<Slug>[] = [
  {
    key: "name",
    label: "slug",
    render: function (slug) {
      return slug.name;
    },
  },
  {
    key: "held",
    label: "held",
    align: "right",
    render: function (slug) {
      return formatNumber(slug.held);
    },
  },
  {
    key: "each",
    label: "shards each",
    align: "right",
    render: function (slug) {
      return formatNumber(slug.each);
    },
  },
  {
    key: "shards",
    label: "shards",
    align: "right",
    title: "craftable is potential, not free: crafting is a manual step",
    render: function (slug) {
      return formatNumber(slug.shards);
    },
  },
];

function shardColumns(point: PointButton): Column<Holder>[] {
  return [
    {
      key: "name",
      label: "building",
      sort: function (holder) {
        return holder.name || "";
      },
      render: function (holder) {
        return holder.name || "–";
      },
    },
    {
      key: "clock",
      label: "clock",
      align: "right",
      sort: function (holder) {
        return holder.clock;
      },
      render: function (holder) {
        return formatNumber(holder.clock * 100) + "%";
      },
    },
    {
      key: "slotted",
      label: "slotted",
      align: "right",
      title: "read from the building's shard slots",
      sort: function (holder) {
        return holder.slotted;
      },
      render: function (holder) {
        return holder.slotted;
      },
    },
    {
      key: "needed",
      label: "needed",
      align: "right",
      title: "what its clock requires",
      sort: function (holder) {
        return holder.needed;
      },
      render: function (holder) {
        return holder.needed;
      },
    },
    {
      key: "idle",
      label: "idle",
      align: "right",
      title: "slotted above what the clock needs",
      sort: function (holder) {
        return holder.idle;
      },
      render: function (holder) {
        return holder.idle || "";
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

export function renderShards(body: HTMLElement, point: PointButton): void {
  const data = shards.data;
  if (!data) {
    waiting(body, shards);
    return;
  }
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", formatNumber(data.free), "crafted shards in stock"));
  tiles.appendChild(tile("craftable", formatNumber(data.craftable), "from slugs on hand, once crafted"));
  tiles.appendChild(tile("slotted", data.measured ? formatNumber(data.committed) : "–", data.measured ? data.idle + " of them above what the clock needs" : "this save does not record slots"));
  tiles.appendChild(tile("owned", formatNumber(data.owned), "free plus slotted"));
  body.appendChild(tiles);
  appendNote(
    body,
    "a shard adds " +
      pct(data.per_shard) +
      " max clock and a building takes " +
      data.slots_per_building +
      ", so the ceiling is " +
      pct(data.max_clock)
  );
  data.by_place.forEach(function (place) {
    appendNote(body, (PLACES[place.place] || place.place) + ": " + amountList(place.items));
  });

  if (data.slugs.length) {
    const slugCard = make("section", "dash-card");
    heading(slugCard, "power slugs");
    slugCard.appendChild(table(SLUG_COLUMNS, data.slugs, { caption: "power slugs" }));
    body.appendChild(slugCard);
  }
  const card = make("section", "dash-card");
  heading(card, "overclocked buildings (" + data.holders.length + ")");
  if (!data.holders.length) appendNote(card, "no building holds a shard or runs above 100%");
  else card.appendChild(table(shardColumns(point), data.holders, { sort: shardSort, caption: "overclocked buildings" }));
  body.appendChild(card);
}
