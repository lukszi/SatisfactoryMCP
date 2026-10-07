/* The finder card's table: the columns each kind of row is listed in. */

import { statusChip } from "../../kit/dashkit";
import { coords, formatNumber, metres, perMin } from "../../kit/format";
import { pickupName } from "../drawn/pickups";
import { carriesText, fieldLabel, nodeLabel, nodeRate, runLabel } from "../../dash/world/world-finds";
import { WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { FinderResults } from "./finder";

/** One table row of the card: the index of a result. */
export interface Listed {
  i: number;
}

function column(key: string, label: string, render: (i: number) => string | HTMLElement, right?: boolean): Column<Listed> {
  return {
    key: key,
    label: label,
    align: right ? "right" : undefined,
    className: right ? "dash-nowrap" : undefined,
    render: function (listed) {
      return render(listed.i);
    },
  };
}

/** The columns the card lists `results` in. */
export function resultColumns(results: FinderResults): Column<Listed>[] {
  if (results.kind === "nodes") {
    const nodes = results.rows;
    return [
      column("node", WORDS.node, function (i) {
        return nodeLabel(nodes[i]!);
      }),
      column("status", "status", function (i) {
        return statusChip(nodes[i]!.status);
      }),
      column("rate", "per min", function (i) {
        return nodeRate(nodes[i]!);
      }, true),
      column("distance", "away", function (i) {
        return metres(nodes[i]!.distance_m);
      }, true),
    ];
  }
  if (results.kind === "fields") {
    const fields = results.rows;
    return [
      column("field", WORDS.field, function (i) {
        return fieldLabel(fields[i]!);
      }),
      column("free", WORDS.free, function (i) {
        return perMin(fields[i]!.free, false);
      }, true),
      column("distance", "away", function (i) {
        return metres(fields[i]!.distance_m);
      }, true),
    ];
  }
  if (results.kind === "sites") {
    const sites = results.rows;
    return [
      column("rank", "rank", function (i) {
        return String(sites[i]!.rank);
      }, true),
      column("region", "region", function (i) {
        return sites[i]!.region || "–";
      }),
      column("score", "score", function (i) {
        return formatNumber(sites[i]!.score, 2);
      }, true),
    ];
  }
  if (results.kind === "runs") {
    const runs = results.rows;
    return [
      column("run", WORDS.run, function (i) {
        return runLabel(runs[i]!);
      }),
      column("carries", "carries", function (i) {
        return carriesText(runs[i]!);
      }),
      column("length", "length", function (i) {
        return metres(runs[i]!.length_m);
      }, true),
      column("distance", "away", function (i) {
        return metres(runs[i]!.distance_m);
      }, true),
    ];
  }
  const pickups = results.rows;
  return [
    column("pickup", "pickup", function (i) {
      return pickupName(pickups[i]!.category);
    }),
    column("place", "at", function (i) {
      return coords(pickups[i]!.x_m, pickups[i]!.y_m);
    }, true),
    column("distance", "away", function (i) {
      return metres(pickups[i]!.distance_m);
    }, true),
  ];
}
