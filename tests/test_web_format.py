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
const cases = JSON.parse(process.argv[3]);
console.log(JSON.stringify(cases.map(([v, signed]) => m.mw(v, { signed }))));
"""


def _mw(tmp_path: Path, cases: list[tuple[float, bool]]) -> list[str]:
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


def test_mw_rounds_a_half_away_from_zero_on_both_signs(tmp_path):
    got = _mw(tmp_path, [(1106.5, False), (-1106.5, True), (1106.5, True), (-1106.5, False)])
    assert got == ["1,107 MW", "-1,107 MW", "+1,107 MW", "-1,107 MW"]


def test_mw_keeps_small_negatives_unsigned_zero(tmp_path):
    assert _mw(tmp_path, [(-0.4, True), (0.4, True), (-0.5, True)]) == ["0 MW", "0 MW", "-1 MW"]
