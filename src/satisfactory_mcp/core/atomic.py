"""Replace a file's contents, or leave the file exactly as it was.

``Path.write_text`` truncates first and writes second, so a crash inside that window leaves a
truncated file and a concurrent reader gets a prefix of one; ``os.replace`` closes it. On
Windows the replace can also FAIL with ``PermissionError`` while another process holds the
destination open, having changed nothing -- so a caller treats that as "not written this
time" rather than as loss.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

__all__ = ["write_bytes", "write_text"]

#: Distinguishes two temps written by two threads of one process for one target. ``count`` is
#: documented as atomic with respect to the GIL, which is the guarantee needed.
_SERIAL = itertools.count()


def _tmp_beside(target: Path) -> Path:
    """A temp beside the target, never in the temp directory: ``os.replace`` across
    filesystems is not a rename but a copy, which reopens the window this module closes."""
    return target.with_name(f"{target.name}.{os.getpid()}.{next(_SERIAL)}.tmp")


def _replace_through(
    target: Path, tmp: Path, mode: str, payload: str | bytes, encoding: str | None
) -> Path:
    """Write ``payload`` to ``tmp`` and move it onto ``target``, or leave ``target`` alone.

    ``fsync`` before the replace: the rename can reach the disk before the data it renames,
    and then a power cut leaves a file that is present, named right and empty.
    """
    try:
        with open(tmp, mode, encoding=encoding) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        # The temp is this function's litter whatever went wrong, including a
        # KeyboardInterrupt -- hence BaseException. The original is untouched either way.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` as one indivisible step. Returns the path. A drop-in for
    ``Path.write_text`` down to its newline translation, so the bytes on disk are the same."""
    target = Path(path)
    return _replace_through(target, _tmp_beside(target), "w", text, encoding)


def write_bytes(path: str | Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` as one indivisible step. Returns the path. Separate from
    ``write_text`` because a pickle put through newline translation is not a pickle."""
    target = Path(path)
    return _replace_through(target, _tmp_beside(target), "wb", data, None)
