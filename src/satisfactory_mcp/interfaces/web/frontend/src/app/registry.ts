/* What the page fetches, declared by the module that draws it; load.ts runs the list knowing
 * none of the names. Types-only imports. See frontend/README.md, "The fetch registry". */

import type { ApiError, ApiUrl } from "../api/client";

/** A save event refetches `live`; a world switch refetches both. */
export type Wave = "static" | "live";

export interface Fetcher<T extends ApiError> {
  wave: Wave;
  /** Where this fetch sits in its wave, in tens: which reply lands first is load-bearing. */
  rank: number;
  /** The path, compile-checked against the server's own OpenAPI document; see ApiUrl. */
  path: ApiUrl;
  /** A query string the path takes from a setting, read at each request. */
  query?: () => string;
  /** What the toast calls this on failure: "belts: …", "summary: …". */
  label: string;
  /** Layer-name prefixes to empty when this fetch fails, matched from the first character. */
  clears: string[];
  /** Whether the floor filter runs again after this draw. */
  refilters: boolean;
  /** What to do with the body. The one place a response type is fixed; see `Registered`. */
  draw: (data: T) => void;
  /** Whether this fetch ends the switch; exactly one may say so. */
  settles?: boolean;
  /** A hook after a successful draw, for what is not a draw. */
  after?: () => void;
  /** Its mirror on a failure, run after the layers are cleared and before the toast. */
  failed?: () => void;
}

/** A fetcher whose response type is erased at registration; the call site keeps its own. */
export type Registered = Fetcher<ApiError>;

const entries: Registered[] = [];

export function registerFetch<T extends ApiError>(fetcher: Fetcher<T>): void {
  if (import.meta.env.DEV) {
    const clash = entries.filter(function (other) {
      return other.path === fetcher.path;
    });
    if (clash.length) {
      console.error("two fetchers registered for " + fetcher.path + " — that is two requests");
    }
  }
  entries.push(fetcher as Registered);
}

/** One wave, in the order it is to be issued in, as a copy. */
export function fetchersOf(wave: Wave): Registered[] {
  return entries
    .filter(function (fetcher) {
      return fetcher.wave === wave;
    })
    .sort(function (a, b) {
      return a.rank - b.rank;
    });
}

/** One fetch by path, for refreshing a single layer outside its wave. */
export function fetcherFor(path: ApiUrl): Registered | undefined {
  return entries.filter(function (fetcher) {
    return fetcher.path === path;
  })[0];
}
