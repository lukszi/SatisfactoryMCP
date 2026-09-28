"""format.ts run under node's type stripping; skipped where node is absent."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FORMAT_TS = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "satisfactory_mcp"
    / "interfaces"
    / "web"
    / "frontend"
    / "src"
    / "format.ts"
)

HOOK = """export async function resolve(s, c, n) {
  if (s.startsWith(".") && !/\\.\\w+$/.test(s)) s += ".ts";
  return n(s, c);
}
"""

REGISTER = """import { register } from "node:module";
import { pathToFileURL } from "node:url";
register(pathToFileURL(process.env.FORMAT_HOOK).href);
"""

RUN = """import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
const fn = process.argv[3];
const cases = JSON.parse(process.argv[4]);
const say = { count: m.count, perMin: (n) => m.perMin(n), bare: (n) => m.perMin(n, false) };
console.log(JSON.stringify(cases.map((args) =>
  fn === "signed" ? m.signed(args[0], say[args[1]]) : m[fn](...args))));
"""


def _run(tmp_path: Path, fn: str, cases: list[list]) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for name, text in (("hook.mjs", HOOK), ("register.mjs", REGISTER), ("run.mjs", RUN)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    done = subprocess.run(
        [
            node,
            "--no-warnings",
            "--import",
            (tmp_path / "register.mjs").as_uri(),
            str(tmp_path / "run.mjs"),
            str(FORMAT_TS),
            fn,
            json.dumps(cases),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "FORMAT_HOOK": str(tmp_path / "hook.mjs")},
        check=False,
    )
    if done.returncode != 0 and "Unknown file extension" in done.stderr:
        pytest.skip("this node cannot strip TypeScript types")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _mw(tmp_path: Path, cases: list[tuple[float, bool]]) -> list[str]:
    return _run(tmp_path, "mw", [[v, {"signed": signed}] for v, signed in cases])


def test_num_rounds_a_half_to_even_as_the_tools_format_does(tmp_path):
    cases = [
        (472.5, 0),
        (473.5, 0),
        (-17.5, 0),
        (22.5, 0),
        (52.5, 0),
        (1234.5, 0),
        (0.25, 1),
        (0.35, 1),
        (2.675, 2),
        (0.125, 2),
        (60.0, 1),
        (-0.4, 0),
    ]
    got = _run(tmp_path, "num", [list(c) for c in cases])
    assert got == [
        f"{v:,.{dp}f}".rstrip("0").rstrip(".") if dp else f"{v:,.0f}".replace("-0", "0")
        for v, dp in cases
    ]


def test_coords_and_metres_round_like_the_tools(tmp_path):
    assert _run(tmp_path, "coords", [[472.5, -961.5]]) == [f"{472.5:.0f}, {-961.5:.0f} m"]
    assert _run(tmp_path, "metres", [[696.5], [None]]) == [f"{696.5:.0f} m", "–"]


def test_mw_rounds_a_half_away_from_zero_on_both_signs(tmp_path):
    got = _mw(tmp_path, [(1106.5, False), (-1106.5, True), (1106.5, True), (-1106.5, False)])
    assert got == ["1,107 MW", "-1,107 MW", "+1,107 MW", "-1,107 MW"]


def test_mw_keeps_small_negatives_unsigned_zero(tmp_path):
    assert _mw(tmp_path, [(-0.4, True), (0.4, True), (-0.5, True)]) == ["0 MW", "0 MW", "-1 MW"]


def test_signed_shares_one_sign_rule_across_units(tmp_path):
    cases = [(3, "count"), (-3, "count"), (0, "count"), (-12.34, "perMin"), (0.04, "bare"), (-0.04, "bare")]
    got = _run(tmp_path, "signed", [list(c) for c in cases])
    assert got == ["+3", "-3", "0", "-12.3/min", "0", "0"]
