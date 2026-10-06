"""``resolve_save``: the two field-reported ways the picker betrayed its own output.

Item 6: ``save=`` rejected the very filename the server prints. Every presenter names
a save by ``header["filename"]`` -- the basename -- but ``resolve_save`` treated the
argument purely as a filesystem path, resolved against the server's working directory,
which is nowhere near the save tree. So ``factory_map(save="Han Solo_020826-082843.sav")``
said "save not found" about a file the same server had read seconds earlier. (The tools
that seemed to accept such names merely swallow the resolution failure and answer
without a save -- ``describe_location``'s ``except Exception: pass``.) The rule now:
a name the server prints must resolve, via a FRESH rescan of the tree on miss.

Item 7: two worlds shared the display name "Han Solo" and ``world="Han Solo"``
silently picked whichever sorted newest. A name that matches more than one world is
now refused with the candidates listed -- world_id, save count, newest file and its
mtime, saveVersion -- and ``world=`` accepts the world_id as the disambiguator.

``scan_saves`` is stubbed throughout: these are tests of the resolver's choices, not
of the sidecar's directory walk.
"""

from __future__ import annotations

import time

import pytest

from satisfactory_mcp.core.saveio import projection as proj

_NOW_NS = int(time.time() * 1e9)


def _save(
    filename: str,
    wid: str = "X2faPVKjX06VaRzClNv5KQ",
    mtime_ns: int = _NOW_NS,
    save_version: int = 60,
    session: str = "Han Solo",
    path: str | None = None,
) -> dict:
    """A header row shaped exactly as the sidecar's ``header_info`` emits one."""
    return {
        "path": path or f"C:/saves/76561190000000001/{filename}",
        "filename": filename,
        "session_name": session,
        "save_identifier": wid,
        "save_version": save_version,
        "play_duration_s": 1000,
        "mtime_ns": mtime_ns,
        "size": 1234,
    }


def _stub_scan(monkeypatch, saves: list[dict], root: str = "C:/saves") -> list[int]:
    """Replace the directory walk; returns a call counter the freshness test reads."""
    calls: list[int] = []

    def scan(root_arg=None):
        calls.append(1)
        return {"root": root, "saves": list(saves), "unsupported": []}

    monkeypatch.setattr(proj, "scan_saves", scan)
    return calls


# ------------------------------------------------------- item 6: save= by filename


def test_a_filename_the_server_prints_resolves(monkeypatch):
    """The exact reproduction: the newest manual save, passed back by its printed name."""
    row = _save("Han Solo_020826-082843.sav")
    _stub_scan(monkeypatch, [row])
    got = proj.resolve_save(path="Han Solo_020826-082843.sav")
    assert got == row


def test_filename_match_ignores_case_like_the_disk_does(monkeypatch):
    row = _save("Han Solo_020826-082843.sav")
    _stub_scan(monkeypatch, [row])
    assert proj.resolve_save(path="han solo_020826-082843.SAV") == row


def test_the_same_filename_in_two_account_folders_resolves_to_the_newest(monkeypatch):
    old = _save("Twin.sav", mtime_ns=_NOW_NS - 10**12, path="C:/saves/acctA/Twin.sav")
    new = _save("Twin.sav", mtime_ns=_NOW_NS, path="C:/saves/acctB/Twin.sav")
    _stub_scan(monkeypatch, [old, new])
    assert proj.resolve_save(path="Twin.sav")["path"] == "C:/saves/acctB/Twin.sav"


def test_a_miss_names_the_name_and_where_it_looked(monkeypatch):
    _stub_scan(monkeypatch, [_save("Other.sav")])
    with pytest.raises(proj.SaveError) as exc:
        proj.resolve_save(path="Gone.sav")
    msg = str(exc.value)
    assert "Gone.sav" in msg
    assert "C:/saves" in msg
    assert "1 readable save(s)" in msg


def test_the_scan_is_fresh_on_every_miss_not_a_snapshot(monkeypatch):
    """A manual save written seconds ago is the name MOST worth resolving, so the
    lookup must hit the disk each time rather than any cached directory listing."""
    saves: list[dict] = []
    calls = _stub_scan(monkeypatch, saves)
    with pytest.raises(proj.SaveError):
        proj.resolve_save(path="Fresh.sav")
    saves.append(_save("Fresh.sav"))  # the player saves between the two calls
    assert proj.resolve_save(path="Fresh.sav")["filename"] == "Fresh.sav"
    assert len(calls) == 2


def test_a_real_path_still_reads_its_own_header(monkeypatch, tmp_path):
    """An existing file must keep going through ``--header-only`` -- it may live
    outside the save tree entirely, where no scan would find it."""
    f = tmp_path / "Elsewhere.sav"
    f.write_bytes(b"\0")
    header = _save("Elsewhere.sav", path=str(f))

    def fake_sidecar(args, timeout=180.0):
        assert args == [str(f), "--header-only"]
        return {"header": header}

    monkeypatch.setattr(proj, "_run_sidecar", fake_sidecar)
    monkeypatch.setattr(
        proj, "scan_saves", lambda root=None: pytest.fail("an existing path needs no scan")
    )
    assert proj.resolve_save(path=str(f)) == header


# --------------------------------------------------- item 7: ambiguous world names


def _two_han_solos(monkeypatch) -> tuple[dict, dict]:
    """Two worlds that share a display name -- the reproduction from the field."""
    a = _save("Han Solo_020826-082843.sav", wid="X2faPVKjX06VaRzClNv5KQ", save_version=60)
    b = _save(
        "Han Solo_autosave_0.sav",
        wid="olderWorldIdQQQQQQQQQQ",
        save_version=28,
        mtime_ns=_NOW_NS - 10**13,
    )
    _stub_scan(monkeypatch, [a, b])
    return a, b


def test_a_name_two_worlds_share_is_refused_with_the_candidates(monkeypatch):
    a, b = _two_han_solos(monkeypatch)
    with pytest.raises(proj.SaveError) as exc:
        proj.resolve_save(world="Han Solo")
    msg = str(exc.value)
    assert "refusing" in msg
    assert "Pass the world_id" in msg
    # Each candidate is listed with everything needed to pick: id, saves, newest
    # file with its mtime, and the saveVersion that tells the two apart.
    for wid in (a["save_identifier"], b["save_identifier"]):
        assert wid in msg
    assert "saveVersion 60" in msg
    assert "saveVersion 28" in msg
    assert "1 save(s)" in msg
    assert "written" in msg


def test_the_world_id_is_the_disambiguator_the_refusal_offers(monkeypatch):
    a, b = _two_han_solos(monkeypatch)
    assert proj.resolve_save(world="olderWorldIdQQQQQQQQQQ") == b
    assert proj.resolve_save(world="X2faPVKjX06VaRzClNv5KQ") == a


def test_a_unique_display_name_still_just_works(monkeypatch):
    row = _save("Solo.sav", session="Calculator")
    _stub_scan(monkeypatch, [row, _save("Other.sav", wid="w2", session="Other World")])
    assert proj.resolve_save(world="Calculator") == row


def test_an_unknown_world_still_lists_what_exists(monkeypatch):
    _two_han_solos(monkeypatch)
    with pytest.raises(proj.SaveError, match="no world matching"):
        proj.resolve_save(world="Chewbacca")
