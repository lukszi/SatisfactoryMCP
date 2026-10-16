/* The World page's address, `world?show=nodes|conduits|pickups&…&sel=<key>`, and where the old
 * `world/<view>?…` addresses land. Pure. docs/frontend_kit.md, "World". */

/* Not nav.ts's: that one imports the map. */
function decodeOrKeep(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch (_e) {
    return text;
  }
}

export type Show = "nodes" | "conduits" | "pickups";

export const SHOWS: Show[] = ["nodes", "conduits", "pickups"];

export const GROUPS: Record<Show, string[]> = {
  nodes: ["none", "field", "region"],
  conduits: ["run", "network"],
  pickups: [""],
};

export interface WorldAddress {
  show: Show;
  /** A resource's class id; "" is any. */
  res: string;
  /** "me", a factory's name, "x,y" or a selector the finders take. */
  near: string;
  group: string;
  sort: string;
  /** Pickups: remaining or collected. */
  mode: "remaining" | "collected";
  /** Pickups: a category; conduits: belt or pipe. */
  kind: string;
  /** Conduits: the search radius in metres; "" is the server's default. */
  radius: string;
  /** Filters old links carry that the controls do not show: purity, status, within, pure, net. */
  extra: Record<string, string>;
  sel: string;
}

const EXTRA: Record<Show, string[]> = {
  nodes: ["purity", "status", "within", "pure"],
  conduits: ["net"],
  pickups: [],
};

/** The sorts a list offers, the first being the default. */
export function sortsOf(a: Pick<WorldAddress, "show" | "group">): string[] {
  if (a.show === "pickups") return ["nearest", "kind"];
  if (a.show === "conduits") return a.group === "network" ? ["distance", "length"] : ["length", "distance"];
  if (a.group === "field") return ["nearest", "total", "rank"];
  return ["nearest", "rate"];
}

function params(subject: string): Record<string, string> {
  const out: Record<string, string> = {};
  const cut = subject.indexOf("?");
  (cut < 0 ? subject : subject.slice(cut + 1)).split("&").forEach(function (pair) {
    const eq = pair.indexOf("=");
    if (eq > 0) out[pair.slice(0, eq)] = decodeOrKeep(pair.slice(eq + 1));
  });
  return out;
}

function settle(p: Record<string, string>): WorldAddress {
  const show = SHOWS.indexOf(p.show as Show) >= 0 ? (p.show as Show) : "nodes";
  const groups = GROUPS[show];
  const group = groups.indexOf(p.group || "") >= 0 ? p.group! : groups[0]!;
  const res = show === "nodes" ? p.res || "" : "";
  const sorts = sortsOf({ show, group });
  let sort = sorts.indexOf(p.sort || "") >= 0 ? p.sort! : sorts[0]!;
  if (sort === "rank" && !res) sort = sorts[0]!;
  const extra: Record<string, string> = {};
  EXTRA[show].forEach(function (key) {
    if (p[key]) extra[key] = p[key]!;
  });
  return {
    show,
    res,
    near: p.near || "me",
    group,
    sort,
    mode: show === "pickups" && p.mode === "collected" ? "collected" : "remaining",
    kind: show === "nodes" ? "" : p.kind || "",
    radius: show === "conduits" ? p.radius || "" : "",
    extra,
    sel: p.sel || "",
  };
}

/** `subject` is what follows "world": "" or "?show=…". */
export function worldAddress(subject: string): WorldAddress {
  return settle(subject && subject.charAt(0) === "?" ? params(subject) : {});
}

export function worldDash(a: Partial<WorldAddress>): string {
  const s = settle({ show: a.show || "", group: a.group || "" });
  const full = settle({
    show: s.show,
    group: s.group,
    res: a.res || "",
    sort: a.sort || "",
    mode: a.mode || "",
    kind: a.kind || "",
    radius: a.radius || "",
    near: a.near || "",
    sel: a.sel || "",
    ...(a.extra || {}),
  });
  const parts: string[] = [];
  function put(key: string, value: string, fallback: string): void {
    if (value && value !== fallback) parts.push(key + "=" + encodeURIComponent(value));
  }
  put("show", full.show, "nodes");
  put("res", full.res, "");
  put("near", full.near, "me");
  put("group", full.group, GROUPS[full.show][0]!);
  put("sort", full.sort, sortsOf(full)[0]!);
  put("mode", full.mode, "remaining");
  put("kind", full.kind, "");
  put("radius", full.radius, "");
  Object.keys(full.extra)
    .sort()
    .forEach((key) => put(key, full.extra[key]!, ""));
  put("sel", full.sel, "");
  return parts.length ? "world?" + parts.join("&") : "world";
}

/** What a scroll position belongs to: the query without the pick. */
export function worldScrollKey(subject: string): string {
  return worldDash({ ...worldAddress(subject), sel: "" });
}

/** An old World view and its parameters as the current address. */
export function fromLegacy(view: string, p: Record<string, string>): string {
  const near = p.near || "";
  if (view === "nodes" || view === "fields" || view === "regions") {
    const ranked = view === "fields" && p.rank === "1";
    return worldDash({
      show: "nodes",
      res: p.resource,
      near: ranked ? p.at : near,
      group: view === "fields" ? "field" : view === "regions" ? "region" : "none",
      sort: ranked ? "rank" : "",
      extra: { purity: p.purity || "", status: p.status || "", within: ranked ? p.within_m || "" : "", pure: ranked ? p.pure || "" : "" },
    });
  }
  if (view === "rank") {
    return worldDash({ show: "nodes", res: p.resource, near: p.at, group: "field", sort: "rank", extra: { within: p.within_m || "", pure: p.pure || "" } });
  }
  if (view === "conduits") {
    return worldDash({
      show: "conduits",
      near,
      radius: p.radius_m,
      kind: p.conduit_kind,
      group: p.view === "networks" ? "network" : "run",
      extra: { net: p.network || "" },
    });
  }
  if (view === "pickups") return worldDash({ show: "pickups", near, kind: p.group, mode: p.view === "collected" ? "collected" : "remaining" });
  return "world";
}

/** Where an old `world/<view>?…` address lands; null when `subject` is current. */
export function legacyWorldDash(head: string, subject: string): string | null {
  if (head !== "world" || !subject || subject.charAt(0) === "?") return null;
  const cut = subject.indexOf("?");
  return fromLegacy(cut < 0 ? subject : subject.slice(0, cut), cut < 0 ? {} : params(subject.slice(cut + 1)));
}
