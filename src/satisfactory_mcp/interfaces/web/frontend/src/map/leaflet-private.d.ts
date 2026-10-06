/* The fields this page hangs off Leaflet objects, declared so that `L` can be typed.
 *
 * The underscore marks are the page's own, optional because only some objects get one;
 * `_handlingClick`, `_update`, `layerId` and `_getBoundsOffset` are Leaflet 1.9.4 internals
 * to check before an upgrade. frontend/README.md has the longer note.
 */

import type * as L from "leaflet";
import "leaflet";

/* What the floor filter joins one drawn piece by: a mark rather than a lookup table, because
 * the filter walks every piece. The fields are alternatives, one per way a layer is joined;
 * floors/floors.ts lists the joins. */
export interface FloorMark {
  /** An instance leaf: how a band lists its machines and its belt attachments. */
  id?: string;
  /** A belt CHAIN or a pipe row, and which of the two number spaces it is in. */
  run?: { kind: "belt" | "pipe"; key: number };
  /** Which piece of the power grid this is: a `wire` (two `ends`, the one kind that can leave a
   *  floor), a `pole` (one point, placed like storage), or the `casing` drawn under either,
   *  which gets the same verdict and never a glyph. */
  power?: "wire" | "pole" | "casing";
  /** The two ends of this piece in game metres, so a connector's glyph can be put on the
   *  end that is actually on this floor. `[x, y, z]`, the payload's own order. */
  ends?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** Where each wire end COUNTS AS STANDING, against `ends`, where it is drawn: the base of the
   *  pole it ends at, or the endpoint where no pole is named. Absent means "judge by ends". */
  anchors?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** A piece's position in `/api/structures`, which is what `deck_rows` indexes. */
  row?: number;
  /** Where it stands, in game metres. For storage, which no band lists, and for the
   *  height a machine occupies above its own deck. */
  x_m?: number;
  y_m?: number;
  z_m?: number;
  /** How tall it is, from the same clearance box as its footprint. Absent where the docs
   *  dump carries none, which is where no claim about piercing a ceiling can be made. */
  h_m?: number | null;
}

declare module "leaflet" {
  interface Layer {
    /** The ROW RANK: where this layer's row sits in the control, as [band, slot, name].
     *  Declared at the `clearedLayer()` call that creates the group; see BAND in layers.ts. */
    _rank?: [number, number, string];
    /** What the floor filter joins this piece by. See FloorMark. */
    _floor?: FloorMark;
    /** Everything a LayerGroup held before the floor filter took some of it away.
     *
     * On the GROUP, not on a piece: the filter replaces a group's contents and leaving is
     * putting them back. Cleared by `clearedLayer()` along with the contents themselves -- a
     * snapshot of data that has been refetched is a claim about a world that is gone. */
    _floorAll?: L.Layer[];
  }

  interface Path {
    /** A direction mark rather than a route: styled by opacity, never by weight. */
    _chevron?: boolean;
    /** A glyph whose radius is a fixed pixel size rather than one derived from the scale.
     *
     * Set on the power poles. Read twice in routes.ts: styleRoutes leaves such a piece's
     * radius alone, and sinkRoutes puts it above the runs it terminates rather than under. */
    _fixed?: boolean;
    /** How this path was drawn before it was ghosted, so unghosting is exact rather than a
     *  second guess at the drawing module's own options. Its presence IS "this path is
     *  ghosted right now". See ghost() in floors/glyphs.ts. */
    _floorStyle?: L.PathOptions;
    /** ...and the CONTENT of the card it carried, so an unghosted machine stops saying what a
     *  ghost says. The content and not the popup: `bindPopup` REUSES an existing popup when
     *  handed a string. No function case, because no popup on this page is one. */
    _floorCard?: string | HTMLElement | null;
    /** Extra stroke width in SCREEN pixels: a casing's fixed rim, re-added at every zoom.
     *  See "Cased lines" in docs/frontend_palette.md. */
    _widen?: number;
    /** The route this polyline was tessellated FROM, kept so it can be tessellated again at
     *  another scale: the drawn latlngs are an output and cannot be re-subdivided from
     *  themselves. See routeShape and styleRoutes in routes.ts. */
    _route?: import("./geometry").RouteShape;
    _occupied?: boolean;
  }

  interface Marker {
    /** Declutter priority. A factory's machine count: big factories win. */
    _labelWeight?: number;
    _labelName?: string;
  }

  interface Map {
    _getBoundsOffset(pxBounds: L.Bounds, maxBounds: L.LatLngBounds, zoom?: number): L.Point;
  }

  namespace Control {
    interface Layers {
      /** Leaflet's own re-render suppressor, borrowed by batch(). */
      _handlingClick: boolean;
      /** Leaflet's own list render. Wrapped by this page, and called once per batch. */
      _update(): void;
    }
  }
}

/* Leaflet's own "am I on a map", which `@types/leaflet` declares `protected` on `Layer`
 * and this page reads from outside. It cannot go in the augmentation above -- redeclaring a
 * protected member as public is an error -- so it is a view type. `map.hasLayer` is NOT the
 * same question: these layers are inside a LayerGroup, so the map's own registry holds the
 * group and not them. */
export interface OnMap {
  _map?: L.Map;
}

/** A checkbox Leaflet built for a control row: it carries the layer's stamp. */
export interface LayerInput extends HTMLInputElement {
  layerId: number;
}

/** Half of a section head: which family it belongs to, and which of its two controls it is. */
export interface SectionPart extends HTMLElement {
  _section?: string;
  _part?: "box" | "fold";
}

/** A DOM mouse event that has already opened an inspector card. See inspect(). */
export interface InspectedEvent extends MouseEvent {
  _inspected?: boolean;
  _machine?: { leaf: string; name: string };
}
