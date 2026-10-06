/* The fields this page hangs off Leaflet objects, declared so that `L` can be typed.
 *
 * The underscore marks are the page's own, optional because only some objects get one;
 * `_handlingClick`, `_update`, `layerId` and `_getBoundsOffset` are Leaflet 1.9.4 internals
 * to check before an upgrade. frontend/README.md has the longer note.
 */

import type * as L from "leaflet";
import "leaflet";

/* What the floor view needs to know about one drawn piece: the JOIN to `/api/floors`, never
 * the answer. A mark rather than a lookup table, because the filter walks tens of thousands
 * of pieces. The fields are alternatives, one per way a layer is joined: machine id, belt or
 * pipe key, structure row, storage position, wire ends. */
export interface FloorMark {
  /** An instance leaf: how a band lists its machines and its belt attachments. */
  id?: string;
  /** A belt CHAIN or a pipe row, and which of the two number spaces it is in. */
  run?: { kind: "belt" | "pipe"; key: number };
  /* Which piece of the power grid this is. The layer is placed geometrically, on this side of
   * the wire, because `/api/floors` has never carried one.
   *
   *   `wire`   -- a drawn chord; `ends` carries both endpoints, and the two z values are
   *               what decide which floors it touches. The one kind that can earn a
   *               connector glyph, because it is the only one that can leave a floor.
   *   `pole`   -- a mark at one point; `x_m`/`y_m`/`z_m`, exactly like storage.
   *   `casing` -- the darker piece drawn under one of the other two. Same geometry, same
   *               answer, no popup and no glyph. Marked so the filter keeps a pair together
   *               without having to know it is a pair. See drawPower in power-wires.ts. */
  power?: "wire" | "pole" | "casing";
  /** The two ends of this piece in game metres, so a connector's glyph can be put on the
   *  end that is actually on this floor. `[x, y, z]`, the payload's own order. */
  ends?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** Where each end COUNTS AS STANDING for the floor filter, against `ends`, which is where
   *  it is drawn. A wire's endpoint is a connector -- 7 m over a Mk1 pole's base, 24 m over
   *  a tower's -- so judging storeys by `ends` misfiles cables around 1-2 m mezzanine
   *  half-bands. Where the server names the pole an end terminates at, its entry here is
   *  that pole's own base; where it does not, it is the endpoint itself. Only wires carry
   *  it; absent means "judge by ends". */
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
     *  Declared at the `layer()` call that creates the group; see BAND in layers.ts. */
    _rank?: [number, number, string];
    /** What the floor filter joins this piece by. See FloorMark. */
    _floor?: FloorMark;
    /** Everything a LayerGroup held before the floor filter took some of it away.
     *
     * On the GROUP, not on a piece: the filter replaces a group's contents and leaving is
     * putting them back. Cleared by `layer()` along with the contents themselves -- a
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
     *  ghosted right now". See ghost() in floors.ts. */
    _floorStyle?: L.PathOptions;
    /** ...and the CONTENT of the card it carried, so an unghosted machine stops saying what a
     *  ghost says. The content and not the popup: `bindPopup` REUSES an existing popup when
     *  handed a string. No function case, because no popup on this page is one. */
    _floorCard?: string | HTMLElement | null;
    /** Extra stroke width in SCREEN pixels: the casing under a power line, a fixed rim that
     *  styleRoutes re-adds at every zoom. See WIRE_CASING_PX in power-wires.ts. */
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
