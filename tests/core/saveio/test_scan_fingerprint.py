"""The scan is memoised on the state of the disk, and the memo must not cost freshness.

``scan_saves`` is what notices the save the player wrote a moment ago, and everything above
it -- which save is newest, which world it belongs to, which projection key to look up -- is
downstream of that. It used to be honest by never remembering anything: a subprocess per
call, ~90 ms, which was the whole floor under a warm read. It now remembers, keyed on a
fingerprint of the tree, so these tests exist to prove it is no slower to notice than the
sidecar it replaced. Every one of them fails as a page that is silently one autosave stale.
"""

from __future__ import annotations

import os
import time

import pytest

from satisfactory_mcp.core.saveio import projection as proj


def write(path, data: bytes, *, settled: bool = True):
    """Write a save, and by default backdate it out of ``_SETTLE_NS``.

    Backdating is what makes a test about the FINGERPRINT: inside the settle window the scan
    is retaken whatever the fingerprint says, so a test that skipped this would pass on a
    fingerprint that saw nothing at all.
    """
    path.write_bytes(data)
    if settled:
        old = time.time() - 3600
        os.utime(path, (old, old))
    return path


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A save root with one nested account folder, and a sidecar that only counts calls.

    No real ``.sav`` bytes anywhere: what is under test is which changes reach the
    fingerprint, and the fingerprint reads no file contents at all.
    """
    proj._SCAN_MEMO.clear()
    account = tmp_path / "76561190000000001"
    account.mkdir()
    write(account / "Han Solo_autosave_0.sav", b"x" * 100)

    spawns: list[list[str]] = []

    def fake_sidecar(args, timeout=180.0):
        spawns.append(args)
        return {"root": str(tmp_path), "saves": [], "unsupported": [], "call": len(spawns)}

    monkeypatch.setattr(proj, "_run_sidecar", fake_sidecar)
    yield tmp_path, account, spawns
    proj._SCAN_MEMO.clear()


# ---- promptness ---------------------------------------------------------


def test_a_written_save_is_seen_by_the_very_next_call(tree):
    """No sleep, no poll, no second chance: write, then scan.

    This is the contract the memo had to buy its speed against, and the reason the previous
    implementation ran a subprocess on every call. A time-based memo would fail here for the
    length of its TTL, and the failure is a browser drawing the world as it was before the
    save the player just made -- with every header in the answer naming the older file, so
    nothing anywhere looks wrong.
    """
    root, account, spawns = tree
    proj.scan_saves(root)
    assert len(spawns) == 1

    write(account / "Han Solo_020826-195005.sav", b"y" * 200, settled=False)
    proj.scan_saves(root)
    assert len(spawns) == 2, "a save written a microsecond ago was answered from the memo"


def test_the_fingerprint_sees_an_in_place_rewrite_by_its_modification_time(tree):
    """The autosave case, which is the only one that happens every five minutes.

    Satisfactory rewrites one filename in place, so nothing about the directory listing
    changes except the modification time -- and a save landing on the same byte count as the
    one before it is ordinary, not exotic. Backdated to both sides of the rewrite, so what
    is asserted is that the mtime is in the fingerprint and not that the guard below caught
    a recent write.
    """
    root, account, spawns = tree
    victim = account / "Han Solo_autosave_0.sav"
    proj.scan_saves(root)

    victim.write_bytes(b"z" * 100)  # same name, same length, new content
    stamp = time.time() - 60
    os.utime(victim, (stamp, stamp))
    proj.scan_saves(root)

    assert len(spawns) == 2, "an in-place rewrite at an unchanged size did not invalidate"


def test_two_writes_inside_one_clock_tick_are_not_mistaken_for_one(tree):
    """Windows stamps modification times in ~15.6 ms steps, so a fingerprint over ``(name,
    size, mtime)`` genuinely cannot tell these apart -- the sidecar it replaced could,
    because it re-read every header. ``_SETTLE_NS`` is what buys that back, and this is the
    test that fails if someone decides the window is not worth the one scan it costs."""
    root, account, spawns = tree
    victim = account / "Han Solo_autosave_0.sav"
    proj.scan_saves(root)

    for expected in (2, 3, 4):
        victim.write_bytes(b"z" * 100)
        proj.scan_saves(root)
        assert len(spawns) == expected, "a rewrite in the same clock tick was not noticed"


def test_a_rewrite_is_seen_while_the_writer_still_holds_the_file_open(tree):
    """``DirEntry.stat`` reads what the directory walk already returned, and on NTFS a
    directory entry is allowed to lag behind an open handle. Measured not to here, and
    pinned, because if it ever does the fingerprint goes stale exactly while the game is
    writing -- which is the one moment it is asked."""
    root, account, spawns = tree
    victim = account / "Han Solo_autosave_0.sav"
    proj.scan_saves(root)

    with open(victim, "r+b") as handle:
        handle.write(b"w" * 64)
        handle.flush()
        os.fsync(handle.fileno())
        stamp = time.time() - 60
        os.utime(victim, (stamp, stamp))
        proj.scan_saves(root)

    assert len(spawns) == 2, "the fingerprint lagged behind a handle that was still open"


def test_a_deleted_save_is_seen(tree):
    """Deletions matter as much as writes: the newest save being gone changes which save
    every answer that names no file is about."""
    root, account, spawns = tree
    doomed = write(account / "Han Solo_020826-152948.sav", b"y" * 200)
    proj.scan_saves(root)

    doomed.unlink()
    proj.scan_saves(root)
    assert len(spawns) == 2, "a deleted save was answered from the memo"


def test_a_save_in_a_new_account_folder_is_seen(tree):
    """The tree is two levels deep -- one folder per Steam account -- so a fingerprint that
    walked only the root would never see a second account's saves appear."""
    root, _account, spawns = tree
    proj.scan_saves(root)

    other = root / "76561198000000001"
    other.mkdir()
    write(other / "Sunset Bay_autosave_0.sav", b"q" * 50)
    proj.scan_saves(root)
    assert len(spawns) == 2, "a save under a new account folder was not noticed"


# ---- the memo -----------------------------------------------------------


def test_a_still_tree_costs_no_subprocess(tree):
    """The point of the whole exercise: the sidecar was 87 ms of a 90 ms warm read."""
    root, _account, spawns = tree
    first = proj.scan_saves(root)
    for _ in range(20):
        assert proj.scan_saves(root) is first
    assert len(spawns) == 1


def test_the_fingerprint_is_cheaper_than_the_sidecar_it_replaces(tree):
    """A budget, not a benchmark: the number it has to beat is 87 ms."""
    root, _account, _spawns = tree
    for _ in range(50):
        proj._tree_fingerprint(root)

    started = time.perf_counter()
    for _ in range(50):
        proj._tree_fingerprint(root)
    each_ms = (time.perf_counter() - started) * 1000 / 50

    assert each_ms < 10, f"{each_ms:.2f} ms per fingerprint of a two-file tree"


def test_two_roots_are_fingerprinted_apart(tree, tmp_path_factory):
    """The root is in the memo key beside the fingerprint. Two empty directories share a
    fingerprint -- the empty tuple -- and answering for one with the other's scan would
    report the wrong ``root`` and no saves at all."""
    root, _account, spawns = tree
    other = tmp_path_factory.mktemp("other-saves")

    proj.scan_saves(root)
    proj.scan_saves(other)
    assert len(spawns) == 2, "a second save root was served the first one's scan"


def test_a_missing_root_never_reaches_the_sidecar(tree):
    root, _account, spawns = tree
    answer = proj.scan_saves(root / "not-a-directory")
    assert answer["missing_root"] is True
    assert spawns == []
