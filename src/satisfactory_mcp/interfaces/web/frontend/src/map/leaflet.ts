/* Leaflet, imported once so that "which Leaflet, and how typed" has one answer.
 *
 * Every other module takes `L` from here rather than from the package, which keeps the places
 * that reach into Leaflet internals greppable; the private fields this page hangs off Leaflet
 * objects are declared in `leaflet-private.d.ts`. The package is pinned to 1.9.4, and those
 * declarations are about that version's internals.
 */

import * as Leaflet from "leaflet";

/* `leaflet-private.d.ts` is NOT imported: it is a declaration file inside the program's
 * `include`, so its `declare module "leaflet"` augmentation applies everywhere without being
 * named. Importing it would ask Rollup to bundle a file that compiles to nothing. */
export const L = Leaflet;
