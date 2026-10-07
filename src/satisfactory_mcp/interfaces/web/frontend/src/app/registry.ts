/* What the page fetches, declared by the module that draws it: a feature says what it wants
 * fetched and what to do with the answer, and load.ts runs the list knowing none of the names.
 *
 * Nothing here is imported at runtime -- everything that draws imports this module, so
 * anything it imported would be evaluated before all of them, and both imports below are
 * `import type`.
 *
 * A registration only exists if the module holding it is in the bundle, and load.ts imports
 * none of them, so main.ts names each one in its FEATURES block and test_frontend_layout.py
 * checks that block against the modules that call `registerFetch`. Without that pair, deleting
 * a feature's last named import drops its layer silently: the build succeeds, the page loads,
 * and the fetch never happens.
 */

import type { ApiError, ApiUrl } from "../api/client";

/* The two waves, which are a claim about the DATA rather than about the code: nodes,
 * concrete and routes change when the player builds; machines, pickups and the header change
 * on every autosave. A save event refetches `live`, a world switch refetches both. */
export type Wave = "static" | "live";

export interface Fetcher<T extends ApiError> {
  wave: Wave;

  /* Where this fetch sits in its wave. Which reply lands first is load-bearing -- floor
   * mode's once-only opening flight measures whatever deck is drawn when it runs, and
   * everything clickable shares one canvas where draw order is hit-testing -- so the order is
   * a decision rather than whatever the import graph produced. Tens, so a feature can be
   * inserted without renumbering. */
  rank: number;

  /** The path, compile-checked against the server's own OpenAPI document; see ApiUrl. */
  path: ApiUrl;

  /** A query string the path takes from a setting, read at each request. */
  query?: () => string;

  /** What the toast calls this on failure: "belts: …", "summary: …". */
  label: string;

  /* Layer-name PREFIXES to empty when this fetch fails, so that a failed switch leaves those
   * layers empty rather than showing the previous world under the new world's header.
   * `clearPrefixed` matches on indexOf === 0, so the trailing space is load-bearing: "node:"
   * would also match a layer called "node:-something-else". */
  clears: string[];

  /* Whether the floor filter runs again after this draw. A redraw replaces a layer's
   * CONTENTS and the filter is a fact about contents, so a layer refetched during floor mode
   * would otherwise arrive holding every storey at once. Stated per fetch because the set is
   * not derivable from floors/filter.ts's own FILTERED list -- the node dots and the factory labels
   * are in here and are not filtered there. */
  refilters: boolean;

  /** What to do with the body. The one place a response type is fixed; see `Registered`. */
  draw: (data: T) => void;

  /* Whether this fetch is the one that ends the switch: the dimmed map and the "loading…"
   * header are cleared when it settles, either way. Exactly one fetch may say so. */
  settles?: boolean;

  /* A hook after a successful draw, for the one thing that is not a draw: /api/machines has
   * to refresh the floor decomposition, because a save write changes what is BUILT and the
   * ids a band lists are what the filter runs on. */
  after?: () => void;

  /* ...and its mirror, run after the layers are cleared and before the toast, for the one
   * failure that has to say something outside a layer: the header's own text. */
  failed?: () => void;
}

/* A fetcher whose response type has been erased to the common `error` branch, because the
 * list is heterogeneous by construction. The erasure happens at registration and the call
 * site keeps the type it declared: `draw: drawNodes` fixes T to NodesResponse, so a fetcher
 * pointing /api/nodes at the pickup drawer is a compile error. */
export type Registered = Fetcher<ApiError>;

const entries: Registered[] = [];

export function registerFetch<T extends ApiError>(fetcher: Fetcher<T>): void {
  if (import.meta.env.DEV) {
    /* Two entries for one path are two requests for one answer -- the trap being one entry
     * per consumer, where /api/summary feeds both the header and the player dot. Said out
     * loud in dev because the only symptom is a duplicate line in the network panel. */
    const clash = entries.filter(function (other) {
      return other.path === fetcher.path;
    });
    if (clash.length) {
      console.error("two fetchers registered for " + fetcher.path + " — that is two requests");
    }
  }
  entries.push(fetcher as Registered);
}

/** One wave, in the order it is to be issued in. A copy: the caller iterates it while the
 *  draws it triggers are free to do anything at all. */
export function fetchersOf(wave: Wave): Registered[] {
  return entries
    .filter(function (fetcher) {
      return fetcher.wave === wave;
    })
    .sort(function (a, b) {
      return a.rank - b.rank;
    });
}

/** One fetch by path, for a caller that wants a single layer refreshed outside its wave. It
 *  goes through here so that its epoch guard, its clears and its toast are the same ones the
 *  wave would have given it. */
export function fetcherFor(path: ApiUrl): Registered | undefined {
  return entries.filter(function (fetcher) {
    return fetcher.path === path;
  })[0];
}
