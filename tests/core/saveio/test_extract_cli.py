"""``--list`` against a save directory the game is rewriting underneath it."""

from __future__ import annotations

import json

from satisfactory_mcp.core.saveio.extract import cli


def test_a_save_deleted_mid_scan_is_skipped_not_fatal(tmp_path, monkeypatch, capsys):
    """The game rotates autosaves, so a file listed a moment ago can be gone by the stat."""
    (tmp_path / "autosave_0.sav").write_bytes(b"\x00" * 16)

    def vanish(path):
        (tmp_path / "autosave_0.sav").unlink()
        raise ValueError("torn header")

    monkeypatch.setattr(cli, "header_info", vanish)
    assert cli.main(["--list", str(tmp_path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert (body["saves"], body["unsupported"]) == ([], [])


def test_a_failed_list_names_the_directory(tmp_path, monkeypatch, capsys):
    def boom(root):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(cli, "list_dir", boom)
    assert cli.main(["--list", str(tmp_path)]) == 1
    body = json.loads(capsys.readouterr().out)
    assert (body["error"], body["path"]) == ("RuntimeError", str(tmp_path))
