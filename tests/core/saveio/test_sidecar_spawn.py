"""How the sidecar subprocess is spawned.

White-box on purpose. The bug this pins produced no error, no log and no partial
output -- every save-reading tool simply hung until its 180 s timeout, and only when
the server was launched as a real MCP server over stdio. It is invisible to every
other test in this suite, because they call the functions directly from a process
whose stdin is a terminal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.saveio import extract
from satisfactory_mcp.core.saveio import projection as proj


def test_sidecar_never_inherits_the_servers_stdin(monkeypatch):
    """The MCP server's stdin IS the client's JSON-RPC pipe.

    ``subprocess.run(capture_output=True)`` redirects stdout and stderr but leaves
    stdin inherited, so the sidecar was handed the protocol stream. Anything that
    touches it blocks for ever, and worse, could consume bytes the client sent to the
    server. Every save tool hung at exactly the 180 s ceiling.
    """
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--list", "somewhere"])

    assert seen.get("stdin") is subprocess.DEVNULL, (
        "sidecar must get DEVNULL, never the inherited MCP protocol pipe"
    )
    assert seen.get("capture_output") is True


def test_sidecar_runs_a_python_interpreter_not_the_console_script(monkeypatch):
    """sys.executable must be the interpreter. If it ever resolved to the
    satisfactory-mcp console script, the sidecar would spawn a SECOND MCP server that
    waits on stdin and emits no JSON -- the same silent hang by a different route."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--header-only"])

    interpreter = seen["cmd"][0].lower()
    assert "python" in interpreter, interpreter
    assert "satisfactory-mcp" not in interpreter
    # ``-m``, not a constructed file path: the child resolves the extractor through the
    # same import machinery this process used, so it cannot run a stale copy.
    assert seen["cmd"][1:3] == ["-m", "satisfactory_mcp.core.saveio.extract"]
    assert seen["cmd"][3:] == ["--header-only"]


def test_the_child_is_pointed_at_this_checkouts_source(monkeypatch):
    """The child needs two packages -- the extractor and ``pioneersav`` -- and ``-m``
    only helps if it resolves them from the tree this process is running from.

    An inherited PYTHONPATH naming an older checkout would otherwise decide it, silently,
    and the symptom would be a projection built by code nobody is looking at.
    """
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setenv("PYTHONPATH", "C:/somewhere/else")
    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--header-only"])

    env = seen["env"]
    src = Path(config.__file__).resolve().parent.parent
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(src)
    # Merged over the real environment, not a replacement for it: the extractor is an
    # ordinary Python program and wants the same PATH and TEMP as everyone else.
    assert "C:/somewhere/else" in env["PYTHONPATH"]
    assert set(os.environ) <= set(env)


def test_a_timeout_is_reported_as_a_save_error_not_a_hang(monkeypatch):
    """A stuck sidecar must surface as a readable message, so the next person sees
    'sidecar timed out' rather than a client-side timeout with no explanation."""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    with pytest.raises(proj.SaveError, match="timed out"):
        proj._run_sidecar(["--list", "somewhere"])


def test_empty_sidecar_output_reports_the_exit_code_and_stderr(monkeypatch):
    """Silence from the sidecar is the failure mode that cost the most time to
    diagnose, so it must name the exit code and carry stderr through."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 9, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    with pytest.raises(proj.SaveError, match="exit 9"):
        proj._run_sidecar(["--list", "somewhere"])


def _fake(returncode: int, stdout: bytes, stderr: bytes = b""):
    """A stub ``subprocess.run``. The whole child, in one line, for the cases below."""

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return run


def test_a_nonzero_exit_is_a_failure_even_when_the_json_parses(monkeypatch):
    """The gap this closes: the exit code was never looked at once stdout parsed.

    A child that writes a complete-looking payload and then dies -- in interpreter shutdown,
    on a MemoryError past the final ``json.dump``, on a kill from outside -- produced a
    payload of unknown completeness, and the caller cached it under a key asserting it was
    the whole world. A thin world that looks well-formed is the dangerous kind, which is the
    same argument the schema in ``_cache_key`` rests on.
    """
    monkeypatch.setattr(proj.subprocess, "run", _fake(3, b'{"schema_version": 16}', b"segfault"))
    with pytest.raises(proj.SaveError, match="exited 3"):
        proj._run_sidecar(["somewhere.sav"])


def test_the_stderr_tail_rides_along_with_every_refusal(monkeypatch):
    """``extract`` prints the traceback to stderr and the exception NAME to stdout.

    So the payload alone says ``AttributeError:`` with an empty detail and the only thing
    that says where is the stream that used to be read exclusively when stdout was empty --
    i.e. never, in the one case where it mattered most.
    """
    payload = b'{"error": "AttributeError", "detail": "", "path": "x.sav"}'
    trace = b'Traceback...\n  File "extract.py", line 700, in extract\nAttributeError: rotation'
    monkeypatch.setattr(proj.subprocess, "run", _fake(1, payload, trace))
    with pytest.raises(proj.SaveError, match="AttributeError") as caught:
        proj._run_sidecar(["x.sav"])
    assert "line 700" in str(caught.value), "the traceback is the only thing that says where"

    # And invalid JSON, which is the third refusal and had the same blind spot.
    monkeypatch.setattr(proj.subprocess, "run", _fake(1, b"not json at all", b"why it happened"))
    with pytest.raises(proj.SaveError, match="why it happened"):
        proj._run_sidecar(["x.sav"])


def test_a_clean_exit_with_stderr_folds_the_tail_into_the_projections_warnings(monkeypatch):
    """The parser's own notes reach a reader instead of being dropped on the floor.

    ``extract`` sends them to stderr deliberately -- stdout has to stay parseable, and a
    diagnostic must not become a projection field that makes two parsers' payloads differ.
    Neither of those reasons says the notes should be discarded, and they were.
    """
    stderr = b"pioneersav: at body offset 12345: skipped an unknown property\n"
    monkeypatch.setattr(
        proj.subprocess, "run", _fake(0, b'{"schema_version": 16, "warnings": []}', stderr)
    )
    payload = proj._run_sidecar(["x.sav"])
    assert len(payload["warnings"]) == 1
    assert "body offset 12345" in payload["warnings"][0]
    # One entry, not one per line: this is the tail of a truncated stream and its first line
    # may be half a line, so splitting it would publish a fragment as if it were a note.
    assert payload["warnings"][0].startswith("the sidecar wrote to stderr")


def test_a_quiet_success_gains_no_warning_at_all(monkeypatch):
    """Otherwise every projection carries a note saying nothing happened."""
    monkeypatch.setattr(proj.subprocess, "run", _fake(0, b'{"warnings": []}', b"   \n"))
    assert proj._run_sidecar(["x.sav"])["warnings"] == []
    # And a payload with no ``warnings`` key at all -- ``--header-only`` and ``--list`` --
    # is left exactly as it arrived rather than growing an empty one.
    monkeypatch.setattr(proj.subprocess, "run", _fake(0, b'{"header": {}}', b""))
    assert proj._run_sidecar(["x.sav", "--header-only"]) == {"header": {}}


def test_a_schema_bump_makes_every_cached_projection_miss(monkeypatch):
    """The cache is keyed on the schema, so old pickles are never served to new code.

    A projection is cached on disk under a hash of the save's identity, and the identity has
    to include the shape it was written in. Without that, schema 20 would hand out a schema-19
    pickle -- one whose belt rows carry no actor index, so every belt-fed machine loses the
    run id that says which belt to go and look at, and whose curve column sits one place to
    the left, so every bending belt is drawn around the wrong control points. Schema 19 would
    likewise have handed out a schema-18
    pickle -- one whose ``inventories`` still buckets a dead pioneer's pockets as machine
    buffers and has no ``crate`` bucket at all -- as 18 would have handed out a 17 with no
    ``crates`` key, so a world where the player died in the middle of nowhere carrying half a
    factory would report nothing lying on the ground; 17 a 16 with no ``power`` key and a map
    that draws wires drawing none; and 16 a 15 that buckets eight containers' contents as
    unspendable machine buffers and calls an unreadable rotation axis-aligned.
    The stale answer is the dangerous one precisely because it is well-formed: it looks like a
    world that is poorer than it is and squarer than it is. Every field of the key is asserted
    so that dropping one is a failure here rather than a stale answer months later.
    """
    header = {"path": "C:/saves/Han Solo.sav", "mtime_ns": 1785272928137058500, "size": 2935845}
    now = proj._cache_key(header)

    monkeypatch.setattr(proj, "SCHEMA_VERSION", 21)
    assert proj._cache_key(header) != now, "a schema 21 pickle would be served to schema 22"

    monkeypatch.setattr(proj, "SCHEMA_VERSION", 22)
    assert proj._cache_key(header) == now
    for field, other in (("path", "C:/saves/Other.sav"), ("mtime_ns", 1), ("size", 1)):
        assert proj._cache_key({**header, field: other}) != now, field


def test_the_extractor_stamps_the_one_schema_number_the_fixture_carries(projection):
    """One number, declared once, and the committed fixture has to carry it.

    ``projection`` declares the schema and keys the disk cache on it; the extractor imports
    that same constant and STAMPS it into every projection it writes, so the two cannot drift.
    The committed fixture is the copy that can: leave it behind a bump and the suite goes on
    asserting the shape of a projection the server no longer produces.
    """
    assert extract.SCHEMA_VERSION == proj.SCHEMA_VERSION == projection["schema_version"]
