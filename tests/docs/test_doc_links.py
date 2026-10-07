"""Every relative link, anchor and section reference in the docs resolves.

Section numbers are the spec's: DESIGN.md, "The document set", says which file holds which,
and the map's §17 to §41 resolve through the index in docs/spatial-and-map.md. Anchors are
GitHub's heading slugs, so a renamed heading breaks every link to it.
"""

from __future__ import annotations

import os
import re
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from urllib.parse import unquote

from tests.support.paths import REPO_ROOT

DOCS = REPO_ROOT / "docs"
DESIGN = REPO_ROOT / "DESIGN.md"
MAP_INDEX = DOCS / "spatial-and-map.md"
MAP_INDEX_TITLE = "Sections 17 to 42: the map"
#: The documents DESIGN.md's table gives section numbers to, the map's folder aside.
SPEC_DOCS = [
    DESIGN,
    *(
        DOCS / f"{name}.md"
        for name in ("save-projection", "spatial-and-map", "planning", "mcp-surface")
    ),
    *(DOCS / f"{name}.md" for name in ("parked", "residency", "plumbing")),
]
SKIP_DIRS = {"node_modules", "dist", "__pycache__"}
CODE_SUFFIXES = {".py", ".ts", ".css", ".json", ".toml"}

NUM = r"\d+[a-z]?(?:\.\d+[a-z]?)*"
#: Whitespace, including a line break into a comment's or docstring's next line.
SEP = r"(?:[ \t]|\n[ \t]*(?:#:?|//|/?\*+)?[ \t]*)"
NUM_LIST = rf"(?P<nums>{NUM}(?:{SEP}*(?:,|and|to|–){SEP}*§?{NUM})*)"
TITLE = rf"(?:(?:,{SEP}*|{SEP}*\(){SEP}*\"(?P<title>[^\"\n]+)\")?"
QUALIFIED = re.compile(
    rf"(?P<file>[\w./-]+\.md)[)\]`]*,?{SEP}+(?:§[ \t]?|[Ss]ections?{SEP}+){NUM_LIST}{TITLE}"
)
BARE = re.compile(rf"§[ \t]?{NUM_LIST}{TITLE}")
WORDED = re.compile(rf"\b[Ss]ections?{SEP}+{NUM_LIST}{TITLE}")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
#: ``§24 Plumbing``, ``7. Spatial model``, ``13a. ...``, ``7.2a Node lookup``; not ``16384 px``.
NUMBERED = re.compile(rf"^(?:§({NUM})\.?|(\d+[a-z]?)\.|(\d+[a-z]?(?:\.\d+[a-z]?)+)\.?)(?:\s|$)")
LINK = re.compile(r"(?<!\\)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]+)(?:[ \t]+\"[^\"]*\")?\)")
FENCE = re.compile(r"^[ \t]*(```|~~~)")
Where = Callable[[int], str]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: rendered text, lower case, punctuation but - and _ gone."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "")
    return "".join(c for c in text.lower() if c.isalnum() or c in " -_").replace(" ", "-")


@dataclass
class Doc:
    """A markdown file with its fenced code blanked out and its headings found."""

    path: Path
    lines: list[str]
    #: (line index, level, text) of every heading.
    headings: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def anchors(self) -> set[str]:
        seen: dict[str, int] = {}
        out = set()
        for _, _, text in self.headings:
            base = slug(text)
            out.add(f"{base}-{seen[base]}" if base in seen else base)
            seen[base] = seen.get(base, 0) + 1
        for line in self.lines:
            out.update(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", line))
        return out

    def numbers(self) -> dict[str, int]:
        """Each numbered heading's number, against its heading's position in ``headings``."""
        out: dict[str, int] = {}
        for position, (_, _, text) in enumerate(self.headings):
            if match := NUMBERED.match(text):
                out.setdefault(next(g for g in match.groups() if g), position)
        return out

    def section(self, position: int) -> list[str]:
        start, level, _ = self.headings[position]
        later = (i for i, lv, _ in self.headings[position + 1 :] if lv <= level)
        return self.lines[start : next(later, len(self.lines))]

    def titled(self, title: str) -> list[str]:
        position = next(i for i, (_, _, t) in enumerate(self.headings) if t == title)
        return self.section(position)


@cache
def read_doc(path: Path) -> Doc:
    doc = Doc(path, path.read_text(encoding="utf-8").splitlines())
    inside = False
    for index, line in enumerate(doc.lines):
        if FENCE.match(line):
            inside = not inside
            doc.lines[index] = ""
        elif inside:
            doc.lines[index] = ""
        elif match := HEADING.match(line):
            doc.headings.append((index, len(match.group(1)), match.group(2)))
    return doc


def _walk(base: Path, suffixes: set[str]) -> Iterator[Path]:
    for folder, subfolders, files in os.walk(base):
        subfolders[:] = sorted(d for d in subfolders if d not in SKIP_DIRS and d[0] != ".")
        yield from (Path(folder) / f for f in sorted(files) if Path(f).suffix in suffixes)


@cache
def markdown_files() -> tuple[Path, ...]:
    found = sorted(REPO_ROOT.glob("*.md"))
    for base in ("docs", "tools", "src"):
        found += _walk(REPO_ROOT / base, {".md"})
    return tuple(found)


@cache
def code_files() -> tuple[Path, ...]:
    found = [REPO_ROOT / "pyproject.toml"]
    for base in ("src", "tools", "tests"):
        found += _walk(REPO_ROOT / base, CODE_SUFFIXES)
    return tuple(found)


def map_docs() -> list[Path]:
    return [MAP_INDEX, *sorted((DOCS / "map").glob("*.md"))]


def _readable(path: Path) -> str:
    """The text a reference is looked for in: a doc without its code, a source file whole."""
    if path.suffix != ".md":
        return path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), read_doc(path).text)


def _where(path: Path, text: str) -> Where:
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    rel = path.relative_to(REPO_ROOT).as_posix()
    return lambda offset: f"{rel}:{bisect_right(starts, offset)}"


def _numbers_in(listed: str) -> list[str]:
    """``27, 30 to 33 and 36`` as every number it names, a range of whole sections expanded."""
    out: list[str] = []
    ranged = False
    for word, number in re.findall(rf"(to|–)|({NUM})", listed):
        if word:
            ranged = True
        elif ranged and out and out[-1].isdigit() and number.isdigit():
            out += [str(n) for n in range(int(out[-1]) + 1, int(number) + 1)]
            ranged = False
        else:
            out.append(number)
            ranged = False
    return out


def _has_subsection(lines: list[str], title: str) -> bool:
    """A heading in the section that starts with the title, a bold run-in or a table row."""
    for line in lines:
        heading = HEADING.match(line)
        if heading and heading.group(2).replace("`", "").startswith(title):
            return True
        if line.startswith((f"**{title}", f"| {title}")):
            return True
    return False


class Resolver:
    """Which file holds a section number, by the rules of DESIGN.md's "The document set"."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[Path]] = {}
        for path in markdown_files():
            self.by_name.setdefault(path.name, []).append(path)
        self.map = map_docs()
        self.spec = [*SPEC_DOCS, *self.map[1:]]

    def named(self, name: str) -> list[Path] | None:
        return self.map if name == MAP_INDEX.name else self.by_name.get(name)

    def check(self, match: re.Match, files: list[Path], where: str) -> list[str]:
        numbers = _numbers_in(match.group("nums"))
        holders = {n: next((p for p in files if n in read_doc(p).numbers()), None) for n in numbers}
        problems = [f"{where}: §{n} is not a section there" for n, p in holders.items() if not p]
        title, last = match.group("title"), numbers[-1]
        if title and holders[last]:
            doc = read_doc(holders[last])
            if not _has_subsection(doc.section(doc.numbers()[last]), title):
                problems.append(f'{where}: §{last} has no "{title}"')
        return problems

    def qualified(self, text: str, where: Where) -> tuple[list[str], list[tuple[int, int]]]:
        """``<file>.md §NN`` and ``<file>.md section NN``: the file named decides."""
        problems, spans = [], []
        for match in QUALIFIED.finditer(text):
            spans.append(match.span())
            name = match.group("file").rsplit("/", 1)[-1]
            files = self.named(name)
            if files is None:
                problems.append(f"{where(match.start())}: no {name} to hold the section")
            else:
                problems += self.check(match, files, where(match.start()))
        return problems, spans

    def unqualified(
        self, text: str, pattern: re.Pattern, spans: list, files: list[Path], where: Where
    ) -> list[str]:
        problems = []
        for match in pattern.finditer(text):
            if not any(a <= match.start() < b for a, b in spans):
                spans.append(match.span())
                problems += self.check(match, files, where(match.start()))
        return problems


@cache
def resolver() -> Resolver:
    return Resolver()


def _is_mapgen(path: Path) -> bool:
    return path.relative_to(REPO_ROOT).parts[:2] in {("tools", "mapgen"), ("tests", "mapgen")}


def test_every_relative_link_and_anchor_resolves():
    broken = []
    for path in markdown_files():
        text = _readable(path)
        where = _where(path, text)
        for match in LINK.finditer(text):
            target = match.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / unquote(file_part)).resolve() if file_part else path
            if not dest.exists():
                broken.append(f"{where(match.start())}: no file {target}")
            elif anchor and dest.suffix == ".md" and anchor not in read_doc(dest).anchors():
                broken.append(f"{where(match.start())}: no anchor {target}")
    assert not broken, "fix the link, or the heading it pointed at:\n" + "\n".join(broken)


def test_every_section_named_with_its_file_resolves():
    problems = []
    for path in (*markdown_files(), *code_files()):
        text = _readable(path)
        problems += resolver().qualified(text, _where(path, text))[0]
    assert not problems, "a `<file>.md §NN` reference points nowhere:\n" + "\n".join(problems)


def test_every_map_section_reference_resolves():
    """In the map's docs and in mapgen a bare number is the map's first, then the spec's."""
    scope = [*map_docs(), *(p for p in (*markdown_files(), *code_files()) if _is_mapgen(p))]
    files = [*resolver().map, *resolver().spec]
    problems = []
    for path in scope:
        text = _readable(path)
        where = _where(path, text)
        spans = resolver().qualified(text, where)[1]
        problems += resolver().unqualified(text, BARE, spans, files, where)
        problems += resolver().unqualified(text, WORDED, spans, files, where)
    assert not problems, "a map section reference points nowhere:\n" + "\n".join(problems)


def test_every_bare_section_sign_in_the_spec_resolves():
    """Its own document first: inside parked.md, §20 is parked.md's §20."""
    problems = []
    for path in resolver().spec:
        text = _readable(path)
        where = _where(path, text)
        spans = resolver().qualified(text, where)[1]
        own = resolver().map if path in resolver().map else [path]
        problems += resolver().unqualified(text, BARE, spans, [*own, *resolver().spec], where)
    assert not problems, "a § reference in the spec points nowhere:\n" + "\n".join(problems)


def _listed(cell: str) -> set[str]:
    """``§6–§6.16, §13a`` as the top-level sections it lists, ``§17–§40`` expanded."""
    out = set()
    for first, last in re.findall(rf"§({NUM})(?:–§({NUM}))?", cell):
        if last.isdigit() and first.isdigit():
            out.update(str(n) for n in range(int(first), int(last) + 1))
        else:
            out.update(n.split(".")[0] for n in (first, last) if n)
    return out


def test_design_lists_every_section_of_every_spec_file():
    problems = []
    for line in read_doc(DESIGN).titled("The document set"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("§"):
            continue
        files = [DESIGN] if "this file" in cells[1] else []
        for target in re.findall(r"\]\(([^)#]+)\)", cells[1]):
            dest = REPO_ROOT / target
            files += sorted(dest.glob("*.md")) if dest.is_dir() else [dest]
        held = {n.split(".")[0] for p in files for n in read_doc(p).numbers()}
        if held != _listed(cells[0]):
            problems.append(f"{cells[0]}: the files hold {sorted(held)}")
    assert not problems, "DESIGN.md's table of the document set is out of date:\n" + "\n".join(
        problems
    )


def test_the_map_index_lists_each_map_section_once():
    held: dict[str, list[str]] = {}
    for path in map_docs():
        for number in read_doc(path).numbers():
            if "." not in number and number != "7":
                held.setdefault(number, []).append(path.name)
    twice = {n: names for n, names in held.items() if len(names) > 1}
    assert not twice, f"a map section number used twice: {twice}"
    rows = {}
    for line in read_doc(MAP_INDEX).titled(MAP_INDEX_TITLE):
        if row := re.match(r"^\| (\d+) \| \[[^\]]+\]\((?:map/)?([\w.-]*)#([^)]+)\)", line):
            number, name, anchor = row.groups()
            rows[number] = (name or MAP_INDEX.name, anchor)
    assert sorted(rows, key=int) == sorted(held, key=int)
    for number, (name, anchor) in rows.items():
        doc = read_doc(MAP_INDEX if name == MAP_INDEX.name else DOCS / "map" / name)
        heading = doc.headings[doc.numbers()[number]][2]
        assert slug(heading) == anchor, f"the index row for §{number} misses its heading"
