"""The machine's own save folder, for the tests that read real saves rather than fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from satisfactory_mcp import config


def saves_root_or_skip() -> Path:
    """The save directory the server would read, or a skip when this machine has none."""
    root = config.saves_root()
    if not root.is_dir():
        pytest.skip("no save directory on this machine")
    return root
