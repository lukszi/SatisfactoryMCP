/* The settings dialog's sections and its search: which rows a section or a query shows. */

import type { Setting } from "../settings";

export interface Section {
  id: string;
  label: string;
  group: string;
}

/** Ids are what `settings=<id>` in the address names. */
export const SECTIONS: Section[] = [
  { id: "spoilers", label: "spoilers", group: "spoilers" },
  { id: "factories", label: "factories", group: "detect factories" },
  { id: "power", label: "power", group: "power" },
  { id: "planner", label: "planner", group: "planner" },
  { id: "advisors", label: "advisors", group: "advisors" },
  { id: "map", label: "map display", group: "map" },
];

export const SECTION_IDS = SECTIONS.map(function (s) {
  return s.id;
});

export function sectionOf(id: string): Section {
  return (
    SECTIONS.filter(function (s) {
      return s.id === id;
    })[0] || SECTIONS[0]!
  );
}

export function inSection<S extends Pick<Setting, "group">>(settings: S[], id: string): S[] {
  const group = sectionOf(id).group;
  return settings.filter(function (s) {
    return s.group === group;
  });
}

export interface SectionHits<S> {
  section: Section;
  rows: S[];
}

/** Every setting whose label or hint holds the query, grouped by section in section order. */
export function searchSettings<S extends Pick<Setting, "group" | "label" | "hint">>(settings: S[], query: string): SectionHits<S>[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const hits = settings.filter(function (s) {
    return (s.label + " " + s.hint).toLowerCase().indexOf(q) >= 0;
  });
  return SECTIONS.map(function (section) {
    return {
      section: section,
      rows: hits.filter(function (s) {
        return s.group === section.group;
      }),
    };
  }).filter(function (found) {
    return found.rows.length > 0;
  });
}
