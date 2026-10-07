/* The factory detail page's floors and sites tabs, and the card every tab's sections sit in. */

import { appendNote, chip, empty, heading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { buildingCounts, count, formatNumber } from "../../kit/format";
import { enterFloors } from "../../map/floors/floors";
import { showBox } from "../../map/map-highlight";
import { counted } from "../../kit/words";
import { mapButton } from "../actions";

import type { BboxM } from "../../map/geometry";
import type { FloorBand, FloorPlatform, FloorsResponse, SiteRow, SitesResponse } from "../../api/shapes";

/** What the floors tab reads of a floors reply: a factory with no floors has only a note. */
export type FloorsView = Pick<FloorsResponse, "platforms" | "note">;

/** One section of a tab, under its title. */
export function aspectCard(parent: HTMLElement, title: string): HTMLElement {
  const section = make("section", "dash-card");
  heading(section, title);
  parent.appendChild(section);
  return section;
}

function bandsTable(platform: FloorPlatform, name: string): HTMLElement {
  const bands = platform.bands.slice().reverse();
  return table<FloorBand>(
    [
      {
        key: "floor",
        label: "floor",
        render: function (band) {
          return "floor " + band.ordinal;
        },
      },
      {
        key: "top",
        label: "deck height",
        align: "right",
        render: function (band) {
          return band.top_m === null ? "–" : formatNumber(band.top_m, 1) + " m";
        },
      },
      {
        key: "area",
        label: "area",
        align: "right",
        render: function (band) {
          return formatNumber(band.area_m2, 0) + " m²";
        },
      },
      {
        key: "machines",
        label: "machines",
        align: "right",
        render: function (band) {
          return count(band.machine_count);
        },
      },
      {
        key: "minor",
        label: "",
        render: function (band) {
          return band.minor ? chip("minor", "muted", "under a quarter of the largest deck: a mezzanine or a plinth") : "";
        },
      },
      {
        key: "map",
        label: "",
        render: function (band) {
          return mapButton(
            "show this floor alone on the map",
            function () {
              enterFloors("platform=" + platform.index, name, String(band.ordinal));
            },
            "show floor " + band.ordinal + " on the map"
          );
        },
      },
    ],
    bands,
    { caption: "floors of platform " + platform.index }
  );
}

export function renderFloors(body: HTMLElement, data: FloorsView, name: string): void {
  const platforms = data.platforms.filter(function (platform) {
    return platform.bands.length > 0;
  });
  if (!platforms.length) {
    empty(body, "no floors under this factory", data.note || "it stands on no foundation that has a deck");
    return;
  }
  let section = body;
  platforms.forEach(function (platform) {
    section = aspectCard(body, "platform " + platform.index + (platform.label && platform.label !== name ? " · " + platform.label : ""));
    appendNote(section, counted(platform.bands.length, "floor") + " · " + formatNumber(platform.area_m2, 0) + " m² over " + counted(platform.cells, "tile"));
    section.appendChild(bandsTable(platform, name));
  });
  appendNote(section, "floors are recovered from foundation heights; a platform is poured foundation, and two factories on one slab share it");
}

/* A site is drawn as a square at least 100 m across. */
function siteBox(site: SiteRow): BboxM | null {
  if (site.x_m === null || site.y_m === null) return null;
  const half = Math.max(50, site.diameter_m / 2);
  return [site.x_m - half, site.y_m - half, site.x_m + half, site.y_m + half];
}

export function renderSites(body: HTMLElement, data: SitesResponse): void {
  const section = aspectCard(body, "sites");
  if (!data.sites.length) {
    empty(section, "no site holds this factory's machines");
    return;
  }
  section.appendChild(
    table<SiteRow>(
      [
        {
          key: "where",
          label: "site",
          render: function (site) {
            return site.direction + " · " + site.grid;
          },
        },
        {
          key: "mine",
          label: "this factory",
          align: "right",
          title: "how many of this factory's machines stand in the site",
          render: function (site) {
            return count(site.mine);
          },
        },
        {
          key: "count",
          label: "buildings",
          align: "right",
          title: "every production building in the site, this factory's and any other's",
          render: function (site) {
            return count(site.count);
          },
        },
        {
          key: "spread",
          label: "spread",
          align: "right",
          render: function (site) {
            return formatNumber(site.diameter_m, 0) + " m";
          },
        },
        {
          key: "contents",
          label: "biggest kinds",
          render: function (site) {
            return buildingCounts(site.buildings, ", ", 3, count);
          },
        },
        {
          key: "map",
          label: "",
          render: function (site) {
            const box = siteBox(site);
            if (!box) return make("span", "dash-muted", "–");
            return mapButton(
              "fly the map to this site and outline it",
              function () {
                showBox(box);
              },
              "show site " + site.grid + " on the map"
            );
          },
        },
      ],
      data.sites,
      { caption: "sites" }
    )
  );
  const shared = data.sites.some(function (site) {
    return site.count > site.mine;
  });
  appendNote(
    section,
    "a site is every production building within 300 m of another, named or not" +
      (shared ? "; a site bigger than this factory means it has grown together with its neighbours" : "") +
      " · " + counted(data.total, "site") + " in the world"
  );
}

