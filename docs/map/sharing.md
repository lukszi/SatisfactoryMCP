# The shared map: a public, map-only static site

Section 44 of the [design spec](../../DESIGN.md), indexed in the
[map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 44. The shared map: a public, map-only static site (2026-10-14)

The rendered map can be published as plain files on any web server: the page, one script,
two small JSON files and the tile pictures. No Python, no database, no login on the server.
The page shows the base map, the map-style switch and the sun, and nothing from a save:
no factories, plans, chat, settings or names.

### How it is built

Two pieces:

- **A second build of the page**, `npm run build:share` in
  `src/satisfactory_mcp/interfaces/web/frontend/` (that is `vite build --mode share`). Its entry
  is `share.html` and `src/share.ts`, which load only the map, the mode switch and the sun. In
  this build `SHARE` (`src/share/mode.ts`) is true, and four small branches read static files
  instead of `/api`: the type list from `data/maps.json` (`app/map-types.ts`), the z0 probe's
  headers from `data/site.json` (`map/tiles.ts`, through `share/site.ts`), and every tile from
  a plain path (`map/tiles.ts`, `map/litlayer.ts`). In the normal build `SHARE` is false and
  the bundler drops those branches: the normal `app.js` is byte for byte what it was without
  them. The output goes to `frontend/dist-share/` (gitignored), with content-hashed script and
  style names and no source maps.
- **The export**, `python -m mapgen share-export <out>`. It reads the map registry and the
  layers' sidecars directly (`domain/maps/pyramids.py`, the code the tile routes use), so the
  server need not run, and writes a self-contained folder.

A static server cannot answer `?kind=nrm` or `?px=512` differently from the bare path, and
cannot send per-file custom headers, so the shared page uses neither: each kind of tile has
its own folder, and the probe headers live in `site.json`. The sparse trees tree has no file
where a tile has no trees; the server answers 204 there, a file server 404, and six failures
switch the live sun off. So `site.json` carries a bitmap of the tree tiles that exist (one bit
per tile, levels in order, bit `(4^z - 1) / 3 + y * 2^z + x`), and the page never asks for one
that is not there.

### What the export holds

```
index.html                       the page (mutable)
assets/app-<hash>.js, .css       the page's script and style (immutable: hashed names)
vendor/LEAFLET-LICENSE.txt       Leaflet's licence
data/maps.json, data/site.json   the two manifests (mutable)
t/<layer>-<tag>/tiles/{z}/{x}_{y}.png      baked colour, for browsers without WebGL2
t/<layer>-<tag>/tiles@2x/...               the same at 512 px, for high-density screens
t/<layer>-<tag>/unlit/... .webp            colour for the live sun
t/<layer>-<tag>/trees/... .webp            the painted layer's trees, sparse
t/light-<tag>/{z}/{x}_{y}.{nrm,hz}.webp    normals and horizons, shared by every layer
```

`<tag>` is a digest of the layer's build tags, so a new render lands in new folders and a file
once published never changes. The default is everything the page can use: the ready render
types of the default type's render set (not the game's own artwork), z0 to z7, all four
trees and the light. On build 502094's renders that is 9.96 GB in 188,000 files; z7 is about
70 % of it.

```
python -m mapgen share-export <out> --dry-run             # sizes only, writes nothing
python -m mapgen share-export <out>                       # the full export
python -m mapgen share-export <out> --max-z 6             # 3.6 GB, z6 stretched past it
python -m mapgen share-export <out> --parts lit,baked     # without the @2x tiles
python -m mapgen share-export <out> --ids <id>,<id>       # other types than the default set
```

A rerun copies only files that are not there yet. `--prune` deletes the tile folders and page
assets the new manifests no longer name.

### Nothing private leaves the machine

The site is public, so the export is built from allowlists and checked before it counts:

- **Manifests by allowlist.** `data/maps.json` and `data/site.json` are copied key by key from
  `mapgen/share/manifest.py`; the light model keeps only the numbers the shader reads. No
  paths, sidecar fields, timings, job data, freshness notes or full digests. Each finished
  document is checked against the same lists (`validate`).
- **Images without metadata.** A PNG may hold only pixel and colour chunks, a WebP only its
  bitstream: a text, EXIF or XMP chunk refuses the copy.
- **A scan of every file** (`mapgen/share/privacy.py`) before the manifests are written. It
  refuses a Windows or Unix home path, any Windows path, this checkout's and its data folder's
  paths, an email address, a full git or SHA-256 hash, a `file:` URL, this machine's user and
  host names and git's author name and email (`--private` adds more words), a JSON key off the
  allowlist, a dotfile, a link, and any file type besides HTML, JS, CSS, JSON, PNG, WebP and
  text. In the page it refuses what a strict Content-Security-Policy would block: inline
  scripts, style blocks or attributes, event handler attributes, scripts or styles from
  another origin, `eval`, workers and source maps.

A refused export prints the reasons and keeps the old manifests, so the folder never points
at a half-checked render.

### Serving it with Apache 2.4

[share-apache.conf](share-apache.conf) is a template for a path prefix of an existing HTTPS
site. Replace `@PREFIX@` (say `/map`) and `@DIR@` (the export folder) and include it in the
HTTPS `<VirtualHost>`; on the plain HTTP host, redirect the prefix to HTTPS. What it does:

- Serves the folder as static files only: `Options -Indexes -FollowSymLinks -ExecCGI
  -Includes -MultiViews`, `AllowOverride None`.
- Refuses everything but GET and HEAD, every dotfile, and every file type the export does not
  hold; turns TRACE off.
- Sends a strict CSP (`default-src 'self'`, images also from `data:` and `blob:`, no workers,
  no objects, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'none'`),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a Permissions-Policy
  that turns every feature off, `Cross-Origin-Opener-Policy` and `-Resource-Policy:
  same-origin`, and `X-Robots-Tag: noindex, nofollow`.
- Caches `t/` and `assets/` for a year (`immutable`), and makes the page and the manifests
  revalidate every load (`no-cache`).
- Compresses the script and the manifests only.

There is no login and no rate limit beyond Apache's defaults: `mod_reqtimeout` (on by default)
drops slow clients. A busy site would add `mod_evasive` or a firewall rule.

### Updating it

1. Render, then run the export into the same folder: new folders appear beside the old.
2. Upload `t/` and `assets/` first, without deleting anything: the live site keeps using the
   old folders.
3. Upload `index.html` and `data/` last. That is the switch: the next page load reads the new
   render.
4. Later, export with `--prune` and mirror the deletions in `t/` and `assets/`.

With rsync that is two runs, the second with only `index.html` and `data/`. Any order-keeping
upload works.

### Checking a deployment

```
curl -sI https://<host>@PREFIX@/                      # CSP, nosniff, no-cache
curl -sI https://<host>@PREFIX@/data/site.json        # no-cache, gzip when asked
curl -sI https://<host>@PREFIX@/t/<folder>/0/0_0.hz.webp   # immutable
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<host>@PREFIX@/index.html   # 403
curl -s -o /dev/null -w '%{http_code}\n' https://<host>@PREFIX@/.htaccess            # 403
```

Then open the page with the browser's console showing: a CSP violation is reported there as
"Refused to ...". Before it was published, this setup was checked with the template under a
local Apache 2.4 and a headless Chrome: no violations, live sun and the WebGL-less fallback
both drawing, and a control page with an inline script, a style attribute and a foreign
image refused on all four counts.
