/* The fields this page hangs off Leaflet objects, declared so that `L` can be typed.
 *
 * The underscore marks are the page's own, optional because only some objects get one;
 * `_handlingClick`, `_update`, `layerId` and `_getBoundsOffset` are Leaflet 1.9.4 internals
 * to check before an upgrade. frontend/README.md, "Leaflet's private fields".
 */

import type * as L from "leaflet";
import "leaflet";

/** What the floor filter joins one drawn piece by; the fields are alternatives, one per way a
 *  layer is joined, and floors/floors.ts lists the joins. */
export interface FloorMark {
  /** An instance leaf: how a band lists its machines and its belt attachments. */
  id?: string;
  /** A belt CHAIN or a pipe row, and which of the two number spaces it is in. */
  run?: { kind: "belt" | "pipe"; key: number };
  /** Which piece of the power grid this is: a wire, a pole, or the casing under either. */
  power?: "wire" | "pole" | "casing";
  /** The two ends of this piece in game metres, `[x, y, z]`, as drawn. */
  ends?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** Where each wire end counts as standing; absent means "judge by ends". */
  anchors?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** A piece's position in `/api/structures`, which is what `deck_rows` indexes. */
  row?: number;
  /** Where it stands, in game metres. */
  x_m?: number;
  y_m?: number;
  z_m?: number;
  /** How tall it is, from its clearance box; absent where the docs dump carries none. */
  h_m?: number | null;
}

declare module "leaflet" {
  interface Layer {
    /** The ROW RANK: where this layer's row sits in the control, as [band, slot, name].
     *  Declared at the `clearedLayer()` call that creates the group; see BAND in layers.ts. */
    _rank?: [number, number, string];
    /** What the floor filter joins this piece by. See FloorMark. */
    _floor?: FloorMark;
    /** On a group: everything it held before the floor filter took some of it away. */
    _floorAll?: L.Layer[];
  }

  interface Path {
    /** A direction mark rather than a route: styled by opacity, never by weight. */
    _chevron?: boolean;
    /** A glyph whose radius is a fixed pixel size; read twice in drawn/route-passes.ts. */
    _fixed?: boolean;
    /** How this path was drawn before it was ghosted; present while it is ghosted. */
    _floorStyle?: L.PathOptions;
    /** ...and the content of the card it carried before. */
    _floorCard?: string | HTMLElement | null;
    /** Extra stroke width in SCREEN pixels: a casing's fixed rim, re-added at every zoom.
     *  See "Cased lines" in docs/frontend_palette.md. */
    _widen?: number;
    /** The route this polyline was tessellated from; see drawn/route-geometry.ts. */
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

/* Leaflet's own "am I on a map", `protected` in `@types/leaflet`, as a view type. */
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
