"""``config.docs_path`` refuses to guess.

It used to return the first candidate unchecked, so a machine with no game install was told
the dump was missing from a drive letter it has never had, which reads as a bug in this
server rather than as "the game is somewhere else".
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import config


def test_a_docs_path_that_was_never_found_raises_instead_of_being_returned(monkeypatch, tmp_path):
    monkeypatch.delenv("SATISFACTORY_DOCS", raising=False)
    monkeypatch.setattr(config, "_DOCS_CANDIDATES", (str(tmp_path / "nowhere"),))
    with pytest.raises(FileNotFoundError) as caught:
        config.docs_path()
    said = str(caught.value)
    assert "nowhere" in said, "the message names where it looked"
    assert "SATISFACTORY_DOCS" in said, "and the override that answers it"


def test_a_docs_path_that_was_found_comes_back(monkeypatch, tmp_path):
    monkeypatch.delenv("SATISFACTORY_DOCS", raising=False)
    install = tmp_path / "Satisfactory"
    dump = install / config._DOCS_SUFFIX
    dump.parent.mkdir(parents=True)
    dump.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "_DOCS_CANDIDATES", (str(tmp_path / "nowhere"), str(install)))
    assert config.docs_path() == dump


def test_an_env_var_pointing_at_no_file_is_refused_by_name(monkeypatch, tmp_path):
    monkeypatch.setenv("SATISFACTORY_DOCS", str(tmp_path / "typo.json"))
    with pytest.raises(FileNotFoundError, match="SATISFACTORY_DOCS"):
        config.docs_path()


def test_an_env_var_pointing_at_the_install_directory_is_refused(monkeypatch, tmp_path):
    """The commonest way to set it wrong: the variable names the dump, not the folder."""
    monkeypatch.setenv("SATISFACTORY_DOCS", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="not the install directory"):
        config.docs_path()
