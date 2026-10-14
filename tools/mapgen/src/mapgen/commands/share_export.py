"""Export the rendered map as a public, map-only static site.

``python -m mapgen share-export <out> [--max-z N] [--parts lit,baked,dense] [--ids a,b]
[--dry-run] [--prune]``. Reads the registry and the layers' sidecars directly, so the server
need not run. Tiles go to build-tagged folders under ``t/``, the page to ``assets/``; the two
manifests under ``data/`` and ``index.html`` are written last, and only once every other file
has passed the privacy scan. docs/map/sharing.md.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from mapgen.common import ROOT, Refusal
from mapgen.share import files, manifest, privacy
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.maps import registry

__all__ = ["main"]

PAGE_DIR = ROOT / "src" / "satisfactory_mcp" / "interfaces" / "web" / "frontend" / "dist-share"
#: The deepest level any pyramid has: z7 is 0.23 m a pixel.
FULL_Z = 7


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mapgen share-export", description=__doc__)
    parser.add_argument("out", type=Path, help="the export folder; created, or updated in place")
    parser.add_argument("--max-z", type=int, default=FULL_Z, help="deepest level (default 7)")
    parser.add_argument(
        "--parts", default=",".join(manifest.PARTS), help="lit, baked, dense (default: all)"
    )
    parser.add_argument("--ids", default="", help="layer ids (default: the default's render set)")
    parser.add_argument("--dry-run", action="store_true", help="count files and bytes only")
    parser.add_argument("--prune", action="store_true", help="delete folders no longer named")
    parser.add_argument(
        "--page", type=Path, default=PAGE_DIR, help="the `npm run build:share` output"
    )
    parser.add_argument(
        "--private", action="append", default=[], help="another word no file may hold"
    )
    return parser


def _check_out(out: Path) -> None:
    """Refuse a folder that is not empty and not an earlier export: ``--prune`` deletes in it."""
    if not out.exists():
        out.mkdir(parents=True)
        return
    strays = sorted(p.name for p in out.iterdir() if p.name not in privacy.TOP_LEVEL)
    if strays:
        raise Refusal(
            manifest.REFUSED,
            f"{out} holds {', '.join(strays[:5])}: not an export; pick an empty folder",
        )


def _report(rows: list[tuple[str, files.Count]], total: files.Count, dry: bool) -> None:
    for name, count in rows:
        print(f"{count.bytes / 1e6:10.1f} MB {count.files:8d} files  {name}")
    verb = "would hold" if dry else f"holds ({total.copied} copied this run)"
    print(f"{total.bytes / 1e9:10.2f} GB {total.files:8d} files  {verb}")


def _copies(plans: list[manifest.LayerPlan]) -> list[manifest.TreeCopy]:
    """Every tree once: the light pyramid is shared by the layers drawn on it."""
    seen: dict[str, manifest.TreeCopy] = {}
    for plan in plans:
        for tree in plan.copies:
            seen.setdefault(tree.target, tree)
    return list(seen.values())


def _folders(plans: list[manifest.LayerPlan]) -> set[str]:
    return {p.dir for p in plans} | {p.light for p in plans if p.light}


def _documents(plans: list[manifest.LayerPlan], ids: list[str]) -> tuple[JsonObject, JsonObject]:
    site: JsonObject = {
        "version": 1,
        "render": manifest.render_id(sorted(_folders(plans))),
        "layers": {p.id: p.row() for p in plans},
    }
    return manifest.maps_document(ids), site


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    parts = {p for p in str(args.parts).split(",") if p}
    if not parts <= set(manifest.PARTS) or not parts:
        raise Refusal(manifest.REFUSED, f"--parts takes {', '.join(manifest.PARTS)}")
    ids = [i for i in args.ids.split(",") if i] or manifest.default_ids()
    plans = [manifest.plan_layer(i, args.max_z, parts) for i in ids]
    trees = _copies(plans)
    out: Path = args.out.resolve()
    if args.dry_run:
        rows = [(tree.target, files.count_tree(tree, args.max_z)) for tree in trees]
        total = files.Count()
        for _name, count in rows:
            total.add(count)
        _report(rows, total, True)
        return 0

    _check_out(out)
    assets = files.copy_page(args.page, out)
    rows = [(tree.target, files.copy_tree(tree, out, args.max_z)) for tree in trees]
    total = files.Count()
    for _name, count in rows:
        total.add(count)
    for plan in plans:
        if plan.sparse is not None:
            plan.trees = files.trees_bitmap(plan.sparse, args.max_z)
    maps, site = _documents(plans, ids)
    final = {
        "data/maps.json": files.json_text(maps),
        "data/site.json": files.json_text(site),
        "index.html": (args.page / files.PAGE_ENTRY).read_text(encoding="utf-8"),
    }
    roots = [ROOT, registry.local_dir().parent, Path.home()]
    words = privacy.private_words(args.private)
    problems = [
        *(p for rel in files.MANIFESTS for p in privacy.manifest_problems(rel, final[rel])),
        *(p for rel, text in final.items() for p in privacy.text_problems(rel, text, roots, words)),
        *(p for p in privacy.scan(out, roots, words) if not p.startswith(tuple(final))),
    ]
    if problems:
        print("NOT exported: the folder holds something a public site must not.")
        for problem in problems[:50]:
            print("  " + problem)
        return 1
    for rel, text in final.items():
        files.write_manifest(out, rel, text)
    _report(rows, total, False)
    if args.prune:
        folders = _folders(plans)
        for gone in files.prune(out, folders, set(assets)):
            print(f"pruned {gone}")
    print(
        f"render {site['render']}: {out} passed the privacy scan in {time.monotonic() - started:.0f} s"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except Refusal as refusal:
        print(refusal.message)
        return refusal.code
