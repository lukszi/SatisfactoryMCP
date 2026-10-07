import { describe, expect, it, vi } from "vitest";

import { chevronOpacity, retessellate, routeChevrons, routePolyline } from "../../../src/map/drawn/route-geometry";

import type { Point3M, RouteCurveM } from "../../../src/map/geometry";

/* The map's one coordinate rule, and a polyline that only remembers its points: no Leaflet,
 * no map element. */
vi.mock("../../../src/map/map", () => ({
  latLngOf: (p: number[]) => [-p[1]!, p[0]!],
}));
vi.mock("../../../src/map/leaflet", () => ({
  L: {
    polyline: (latlngs: unknown[]) => ({
      latlngs: latlngs,
      setLatLngs(next: unknown[]) {
        this.latlngs = next;
      },
    }),
  },
}));

type Drawn = ReturnType<typeof routePolyline> & { latlngs: [number, number][] };

/* A 100 m span along x whose tangents bow it 75 m off its chord, peaking mid-span. */
const BOWED: Point3M[] = [
  [10, 20, 0],
  [110, 20, 0],
];
const BOW: RouteCurveM = [
  [
    [0, 300, 0],
    [0, -300, 0],
  ],
];

function drawn(points: Point3M[], curve: RouteCurveM, ppm: number): Drawn {
  return routePolyline(points, curve, ppm, {}) as Drawn;
}

describe("routePolyline", () => {
  it("draws a straight route as its own points at any scale", () => {
    const piece = drawn(BOWED, null, 100);
    expect(piece.latlngs).toEqual([
      [-20, 10],
      [-20, 110],
    ]);
    expect(piece._route).toEqual({ points_m: BOWED, curve_m: null, steps: [1] });
  });

  it("subdivides a curve only once it is visible from its chord", () => {
    expect(drawn(BOWED, BOW, 0.001)._route!.steps).toEqual([1]);
    const piece = drawn(BOWED, BOW, 0.01);
    expect(piece._route!.steps).toEqual([2]);
    expect(piece.latlngs).toEqual([
      [-20, 10],
      [-95, 60],
      [-20, 110],
    ]);
  });

  it("caps the subdivision however close the view", () => {
    const piece = drawn(BOWED, BOW, 1000);
    expect(piece._route!.steps).toEqual([8]);
    expect(piece.latlngs).toHaveLength(9);
  });

  it("measures a span with coincident ends by its control points", () => {
    const lift: Point3M[] = [
      [0, 0, 5],
      [0, 0, 15],
    ];
    const curve: RouteCurveM = [
      [
        [0, 30, 0],
        [0, 0, 0],
      ],
    ];
    expect(drawn(lift, curve, 0.05)._route!.steps).toEqual([1]);
    expect(drawn(lift, curve, 1)._route!.steps).toEqual([4]);
  });

  it("leaves a span without a curve straight inside a curved route", () => {
    const points: Point3M[] = [...BOWED, [210, 20, 0]];
    const piece = drawn(points, [BOW[0]!, null], 0.01);
    expect(piece._route!.steps).toEqual([2, 1]);
    expect(piece.latlngs).toHaveLength(4);
  });
});

describe("retessellate", () => {
  it("redraws only when the step counts change", () => {
    const piece = drawn(BOWED, BOW, 0.001);
    expect(retessellate(piece, 0.002)).toBe(false);
    expect(retessellate(piece, 1000)).toBe(true);
    expect(piece._route!.steps).toEqual([8]);
    expect(piece.latlngs).toHaveLength(9);
    expect(retessellate(piece, 2000)).toBe(false);
  });

  it("never touches a route with no curve, or a piece that is not a route", () => {
    expect(retessellate(drawn(BOWED, null, 1), 1000)).toBe(false);
    expect(retessellate({} as Drawn, 1000)).toBe(false);
  });
});

describe("chevrons", () => {
  it("show only once three metres reach five pixels", () => {
    expect(chevronOpacity(1)).toBe(0);
    expect(chevronOpacity(5 / 3)).toBe(0.7);
  });

  it("space marks evenly by arc length, pointing along the run", () => {
    const run: Point3M[] = [
      [0, 0, 0],
      [48, 0, 0],
    ];
    expect(routeChevrons(run, false)).toEqual([
      [
        [10.5, 1.2],
        [13.5, 0],
        [10.5, -1.2],
      ],
      [
        [34.5, 1.2],
        [37.5, 0],
        [34.5, -1.2],
      ],
    ]);
    const back = routeChevrons(run, true);
    expect(back[0]![1]).toEqual([34.5, 0]);
    expect(back[0]![0]![0]).toBe(37.5);
  });

  it("follow a bend onto its next leg and skip zero-length legs", () => {
    const bent: Point3M[] = [
      [0, 0, 0],
      [30, 0, 0],
      [30, 0, 9],
      [30, 30, 0],
    ];
    const marks = routeChevrons(bent, false);
    expect(marks).toHaveLength(2);
    expect(marks[1]![1]).toEqual([30, 16.5]);
  });

  it("give a short run one mark and a stub none", () => {
    expect(
      routeChevrons(
        [
          [0, 0, 0],
          [10, 0, 0],
        ],
        false
      )
    ).toHaveLength(1);
    expect(
      routeChevrons(
        [
          [0, 0, 0],
          [3, 0, 0],
        ],
        false
      )
    ).toEqual([]);
    expect(routeChevrons([[0, 0, 0]], false)).toEqual([]);
  });
});
