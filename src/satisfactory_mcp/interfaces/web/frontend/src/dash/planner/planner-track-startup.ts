/* Track's startup order card, and the extractors that feed running generators today. See
 * docs/planner-p4_contract.md §2 F5. */

import { copyText } from "../../kit/copy";
import { button, empty, error, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber, mw } from "../../kit/format";
import { goToMapThen } from "../../app/nav";
import { showMachine } from "../../map/map-highlight";
import { loadFeeders } from "./planner-reads";
import { bench } from "./planner-state";
import { fail, notify } from "../../kit/toast";
import { WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { Feeder, TrackResponse } from "../../api/shapes";

export var STARTUP_CTL = "track-startup";

var FEEDERS_TITLE = "what the " + WORDS.stages + " stand on";

/** The last four digits of an instance name, enough to tell two same-named extractors apart. */
function instanceSuffix(instance: string): string {
  const digits = /(\d+)$/.exec(instance);
  return digits ? digits[1]!.slice(-4) : instance.slice(-4);
}

function feederActions(feeder: Feeder): HTMLElement {
  const acts = make("span", "dash-acts");
  const x = feeder.x_m;
  const y = feeder.y_m;
  if (x !== null && y !== null) {
    acts.appendChild(
      button(
        "map",
        function () {
          goToMapThen(function () {
            showMachine(feeder.instance, feeder.name, x as number, y as number, { layers: ["machines"] });
          });
        },
        { map: true, title: "fly the map to this " + feeder.name, label: "show this " + feeder.name + " on the map" }
      )
    );
  }
  acts.appendChild(
    button(
      "copy id",
      function () {
        copyText("machine:" + feeder.instance).then(
          function () {
            notify("copied the id of this " + feeder.name);
          },
          function () {
            fail("could not copy the id: the browser refused");
          }
        );
      },
      { title: "copy this extractor's selector for a tool call", label: "copy the id of this " + feeder.name }
    )
  );
  return acts;
}

function feedersSection(card: HTMLElement): void {
  const feeders = bench.track.feeders;
  if (!feeders) {
    card.appendChild(button(FEEDERS_TITLE, loadFeeders, { title: "list the extractors that feed running generators today (takes about a second)" }));
    return;
  }
  const sub = make("div", "track-feeders");
  sub.appendChild(make("h3", "dash-h", FEEDERS_TITLE));
  if (feeders.busy) loading(sub, FEEDERS_TITLE);
  else if (feeders.error) error(sub, FEEDERS_TITLE, feeders.error, loadFeeders);
  else if (feeders.data && !feeders.data.feeders.length) empty(sub, "no extractor feeds a running generator");
  else if (feeders.data) {
    const twins: Record<string, number> = {};
    feeders.data.feeders.forEach(function (feeder) {
      const place = feeder.name + "|" + feeder.region;
      twins[place] = (twins[place] || 0) + 1;
    });
    const columns: Column<Feeder>[] = [
      {
        key: "name",
        label: "extractor",
        render: function (r) {
          const cell = make("span", "", r.name);
          const place = [r.region || "", (twins[r.name + "|" + r.region] || 0) > 1 ? "#" + instanceSuffix(r.instance) : ""].filter(Boolean).join(" · ");
          if (place) cell.appendChild(make("span", "dash-sub", " " + place));
          return cell;
        },
      },
      {
        key: "mw",
        label: "generation it feeds",
        align: "right",
        render: function (r) {
          return mw(r.mw);
        },
      },
      { key: "acts", label: "", render: feederActions },
    ];
    sub.appendChild(table(columns, feeders.data.feeders, { caption: "extractors feeding running generators" }));
    if (feeders.data.text) sub.appendChild(make("p", "dash-note", feeders.data.text));
  }
  card.appendChild(sub);
}

export function startupCard(parent: HTMLElement, d: TrackResponse): void {
  const startup = d.startup;
  const card = make("section", "dash-card");
  const heading = make("h2", "dash-h", "startup order");
  heading.tabIndex = -1;
  heading.setAttribute("data-ctl", STARTUP_CTL);
  card.appendChild(heading);
  card.appendChild(make("p", "plan-facts", "headroom " + mw(startup.headroom_mw) + ", " + startup.headroom_source));
  card.appendChild(
    make(
      "p",
      "plan-facts",
      "plant draw " + mw(startup.plant_draw_mw) + " · generation " + mw(startup.plant_generation_mw) + " · smallest " + WORDS.stageUnit + " " + mw(startup.minimum_slice_mw)
    )
  );
  startup.warnings.forEach(function (warning) {
    card.appendChild(make("p", "plan-warning", warning));
  });
  d.stages.forEach(function (stage) {
    if (!stage.waits_for_fill) return;
    card.appendChild(make("p", "dash-note", WORDS.stageUnit + " " + stage.index + ": ≥ " + formatNumber(stage.fill_s, 0) + " s before its generators produce"));
  });
  feedersSection(card);
  parent.appendChild(card);
}
