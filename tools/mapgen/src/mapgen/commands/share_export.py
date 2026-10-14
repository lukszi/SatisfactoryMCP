"""Export the rendered map as a public, map-only static site.

``python -m mapgen share-export (<out> | --tar <file or ->) [--max-z N] [--parts lit,baked,dense]
[--ids a,b] [--dry-run] [--prune] [--copy]``. Reads the registry and the layers' sidecars
directly, so the server need not run. Tiles go to build-tagged folders under ``t/``, the page
to ``assets/``; the two manifests under ``data/`` and ``index.html`` come last, and only once
every text file has passed the privacy scan. ``--tar`` writes the same layout as one tar
stream instead of a folder. docs/map/sharing.md.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import os
import sys
import time
from pathlib import Path
from typing import BinaryIO

from mapgen.common import ROOT, Refusal
from mapgen.share import archive, files, manifest, privacy
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.maps import registry

__all__ = ["main"]

PAGE_DIR = ROOT / "src" / "satisfactory_mcp" / "interfaces" / "web" / "frontend" / "dist-share"
#: The deepest level any pyramid has: z7 is 0.23 m a pixel.
FULL_Z = 7


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mapgen share-export", description=__doc__)
    parser.add_argument(
        "out", type=Path, nargs="?", help="the export folder; created, or updated in place"
    )
    parser.add_argument(
        "--tar", metavar="FILE", help="write a tar to FILE (- for stdout) instead of a folder"
    )
    parser.add_argument("--max-z", type=int, default=FULL_Z, help="deepest level (default 7)")
    parser.add_argument(
        "--parts", default=",".join(manifest.PARTS), help="lit, baked, dense (default: all)"
    )
    parser.add_argument("--ids", default="", help="layer ids (default: the default's render set)")
    parser.add_argument("--dry-run", action="store_true", help="count files and bytes only")
    parser.add_argument("--prune", action="store_true", help="delete folders no longer named")
    parser.add_argument(
        "--copy", action="store_true", help="copy tiles even where they could be hard-linked"
    )
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
    placed = f"{total.copied} copied, {total.linked} linked"
    verb = "would hold" if dry else f"holds ({placed} this run)"
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


def _final(
    plans: list[manifest.LayerPlan], ids: list[str], page: Path, max_z: int
) -> dict[str, str]:
    """The manifests and the page's entry, in the order they are written: last of all."""
    for plan in plans:
        if plan.sparse is not None:
            plan.trees = files.trees_bitmap(plan.sparse, max_z)
    maps, site = _documents(plans, ids)
    return {
        "data/maps.json": files.json_text(maps),
        "data/site.json": files.json_text(site),
        "index.html": (page / files.PAGE_ENTRY).read_text(encoding="utf-8"),
    }


def _final_problems(final: dict[str, str], roots: list[Path], words: list[str]) -> list[str]:
    return [
        *(p for rel in files.MANIFESTS for p in privacy.manifest_problems(rel, final[rel])),
        *(p for rel, text in final.items() for p in privacy.text_problems(rel, text, roots, words)),
    ]


def _refused(problems: list[str], what: str) -> int:
    print(f"NOT exported: the {what} holds something a public site must not.")
    for problem in problems[:50]:
        print("  " + problem)
    return 1


def _scope(args: argparse.Namespace) -> tuple[list[str], list[manifest.LayerPlan]]:
    parts = {p for p in str(args.parts).split(",") if p}
    if not parts <= set(manifest.PARTS) or not parts:
        raise Refusal(manifest.REFUSED, f"--parts takes {', '.join(manifest.PARTS)}")
    if (args.out is None) == (args.tar is None) and not args.dry_run:
        raise Refusal(manifest.REFUSED, "name either an export folder or --tar")
    if args.tar is not None and (args.prune or args.copy):
        raise Refusal(manifest.REFUSED, "--prune and --copy are for an export folder")
    ids = [i for i in args.ids.split(",") if i] or manifest.default_ids()
    return ids, [manifest.plan_layer(i, args.max_z, parts) for i in ids]


def _roots() -> list[Path]:
    return [ROOT, registry.local_dir().parent, Path.home()]


def run(args: argparse.Namespace, stdout: BinaryIO | None = None) -> int:
    started = time.monotonic()
    ids, plans = _scope(args)
    trees = _copies(plans)
    if args.dry_run:
        rows = [(tree.target, files.count_tree(tree, args.max_z)) for tree in trees]
        total = files.Count()
        for _name, count in rows:
            total.add(count)
        _report(rows, total, True)
        return 0
    if args.tar is not None:
        return _run_tar(args, ids, plans, trees, stdout, started)

    out: Path = args.out.resolve()
    _check_out(out)
    assets = files.copy_page(args.page, out)
    rows = [(tree.target, files.copy_tree(tree, out, args.max_z, not args.copy)) for tree in trees]
    total = files.Count()
    for _name, count in rows:
        total.add(count)
    final = _final(plans, ids, args.page, args.max_z)
    roots, words = _roots(), privacy.private_words(args.private)
    problems = [
        *_final_problems(final, roots, words),
        *(p for p in privacy.scan(out, roots, words) if not p.startswith(tuple(final))),
    ]
    if problems:
        return _refused(problems, "folder")
    for rel, text in final.items():
        files.write_manifest(out, rel, text)
    _report(rows, total, False)
    if args.prune:
        for gone in files.prune(out, _folders(plans), set(assets)):
            print(f"pruned {gone}")
    render = manifest.render_id(sorted(_folders(plans)))
    print(f"render {render}: {out} passed the privacy scan in {time.monotonic() - started:.0f} s")
    return 0


def _run_tar(
    args: argparse.Namespace,
    ids: list[str],
    plans: list[manifest.LayerPlan],
    trees: list[manifest.TreeCopy],
    stdout: BinaryIO | None,
    started: float,
) -> int:
    """The export as a tar: scanned whole before its first byte, tiles first, ``final`` last."""
    page = files.page_files(args.page)
    final = _final(plans, ids, args.page, args.max_z)
    roots, words = _roots(), privacy.private_words(args.private)
    problems = [
        *privacy.top_problems(rel.split("/")[0] for rel, _path in page),
        *(p for rel, path in page for p in privacy.file_problems(rel, path, roots, words)),
        *_final_problems(final, roots, words),
    ]
    if problems:
        return _refused(problems, "export")
    members = itertools.chain(
        (m for tree in trees for m in archive.tile_members(tree, args.max_z)),
        (archive.Member(rel, path) for rel, path in page),
        (archive.Member(rel, data=text.encode("utf-8")) for rel, text in final.items()),
    )
    if args.tar == "-":
        assert stdout is not None
        total = archive.write(stdout, members)
        stdout.flush()
        where = "stdout"
    else:
        target = Path(args.tar).resolve()
        part = target.with_name(target.name + ".part")
        with part.open("wb") as handle:
            total = archive.write(handle, members)
        os.replace(part, target)
        where = str(target)
    render = manifest.render_id(sorted(_folders(plans)))
    print(
        f"render {render}: {total.files} files, {total.bytes / 1e9:.2f} GB to {where} "
        f"in {time.monotonic() - started:.0f} s"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    to_stdout = args.tar == "-"
    # The tar owns stdout; every message goes to stderr.
    stdout = sys.stdout.buffer if to_stdout else None
    with contextlib.redirect_stdout(sys.stderr) if to_stdout else contextlib.nullcontext():
        try:
            return run(args, stdout)
        except Refusal as refusal:
            print(refusal.message)
            return refusal.code
