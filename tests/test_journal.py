"""The activity journal (contract §8): one append-only file per process, never failing a tool."""

from __future__ import annotations

import json
import os

import pytest

from satisfactory_mcp.domain.planning import journal
from satisfactory_mcp.domain.planning.planlog import Actor

CHAT = Actor("chat", "claude-code", 8248)


@pytest.fixture
def jdir(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.config, "activity_dir", lambda: tmp_path)
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})
    return tmp_path


def test_nothing_is_written_until_the_process_names_itself(jdir):
    assert journal.writer_name() == ""
    assert journal.append("W", "plan.solve", actor=CHAT, text="x") is None
    assert journal.files("W") == []


def test_entries_count_up_per_file_and_carry_provenance(jdir):
    journal.set_writer("chat")
    first = journal.append(
        "W",
        "plan.solve",
        actor=CHAT,
        sav="sav:3f2a91c0aa11",
        tool="plan_factory",
        args={"objective": "min_machines"},
        text="solved HMF 15/min (min_machines)",
    )
    second = journal.append("W", "plan.view", actor=CHAT, plan="a1b2c3d4", rev=3)
    writer = f"chat-{os.getpid()}"
    assert journal.writer_name() == writer
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["id"] == f"{writer}:1"
    assert first["actor"] == {"kind": "chat", "client": "claude-code", "pid": 8248}
    assert (jdir / "W" / f"{writer}.jsonl").is_file()
    assert [e["kind"] for e in journal.read("W")] == ["plan.solve", "plan.view"]


def test_a_writer_name_is_chat_or_web():
    with pytest.raises(ValueError):
        journal.set_writer("page")


def test_seq_continues_after_a_restart_under_the_same_pid(jdir, monkeypatch):
    journal.set_writer("web")
    journal.append("W", "plan.rejected", actor=Actor("page"))
    monkeypatch.setattr(journal, "_seq", {})
    assert journal.append("W", "plan.rejected", actor=Actor("page"))["seq"] == 2


def test_a_torn_tail_is_ignored_by_readers_and_cut_by_the_writer(jdir):
    journal.set_writer("chat")
    journal.append("W", "plan.solve", actor=CHAT, text="one")
    path = journal.files("W")[0]
    with open(path, "ab") as handle:
        handle.write(b'{"id": "half')
    assert [e["text"] for e in journal.read("W")] == ["one"]
    journal.append("W", "plan.solve", actor=CHAT, text="two")
    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b""
    assert [json.loads(x)["text"] for x in lines[:-1]] == ["one", "two"]


def test_an_oversized_entry_drops_its_args_rather_than_the_line(jdir):
    journal.set_writer("chat")
    entry = journal.append(
        "W", "plan.solve", actor=CHAT, args={"sources": ["x" * 50] * 40}, text="t" * 500
    )
    assert entry["args"] is None
    assert len(entry["text"]) == journal.MAX_TEXT
    assert all(len(line) <= journal.MAX_LINE + 1 for line in journal.files("W")[0].open("rb"))


def test_a_failed_write_is_swallowed(jdir, monkeypatch):
    journal.set_writer("chat")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(journal, "_write", boom)
    assert journal.append("W", "plan.solve", actor=CHAT) is None


def test_read_merges_writers_by_time_and_keeps_the_newest(jdir):
    folder = jdir / "W"
    folder.mkdir()
    (folder / "chat-1.jsonl").write_text(
        json.dumps({"id": "chat-1:1", "ts": 1.0})
        + "\n"
        + json.dumps({"id": "chat-1:2", "ts": 3.0})
        + "\n",
        encoding="utf-8",
    )
    (folder / "web-2.jsonl").write_text(
        json.dumps({"id": "web-2:1", "ts": 2.0}) + "\n", encoding="utf-8"
    )
    assert [e["id"] for e in journal.read("W")] == ["chat-1:1", "web-2:1", "chat-1:2"]
    assert [e["id"] for e in journal.read("W", since_ts=1.0)] == ["web-2:1", "chat-1:2"]
    assert [e["id"] for e in journal.read("W", limit=2)] == ["web-2:1", "chat-1:2"]


def test_tail_returns_only_complete_new_lines(jdir):
    path = jdir / "chat-1.jsonl"
    path.write_bytes(b'{"id": "a"}\n{"id": "b"')
    got, offset = journal.tail(path, 0)
    assert [e["id"] for e in got] == ["a"]
    with open(path, "ab") as handle:
        handle.write(b'}\n{"id": "c"}\n')
    got, offset = journal.tail(path, offset)
    assert [e["id"] for e in got] == ["b", "c"]
    assert journal.tail(path, offset) == ([], offset)


def test_a_world_id_is_sanitised_like_the_plan_store(jdir):
    journal.set_writer("chat")
    journal.append("a/b:c", "plan.solve", actor=CHAT)
    assert (jdir / "abc").is_dir()
