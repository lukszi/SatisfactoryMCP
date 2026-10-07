"""A private user data root: every store a test writes lives there, never the reader's."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from satisfactory_mcp import config

#: The ``config`` paths under the user data root, each cached after its first call. Held as
#: the functions themselves, so a test that patches one cannot hide its cache from a clear.
USER_DATA_PATHS = (
    config.plans_dir,
    config.labels_dir,
    config.activity_dir,
    config.ui_dir,
    config.pins_dir,
    config.asks_dir,
    config.advice_dir,
    config.settings_path,
)


def _clear_user_data_caches() -> None:
    for cached in USER_DATA_PATHS:
        cached.cache_clear()


@contextmanager
def private_user_data(root: Path) -> Iterator[Path]:
    """``SATISFACTORY_USER_DATA`` points at ``root`` for the block, and no path is cached across it.

    Its own patch rather than ``monkeypatch``, so a test's ``monkeypatch.undo()`` keeps it.
    """
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("SATISFACTORY_USER_DATA", str(root))
            _clear_user_data_caches()
            yield root
    finally:
        _clear_user_data_caches()
