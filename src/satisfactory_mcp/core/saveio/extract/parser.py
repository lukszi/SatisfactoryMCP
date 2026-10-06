"""The extractor's one door to the save parser, and the only import of ``pioneersav`` in the
application: everything else reaches the parser through the subprocess."""

from __future__ import annotations

import os

import pioneersav

from .readers import truthy

__all__ = ["PARSE_ERROR", "header_info", "read_full_save", "read_save_info"]

read_save_info = pioneersav.read_info
read_full_save = pioneersav.read_full_save

#: What "this save cannot be read" looks like, so the CLI's except clause names one thing.
PARSE_ERROR: tuple[type[BaseException], ...] = (pioneersav.ParseError,)


def header_info(path: str) -> dict:
    """The projection's ``header`` block: the save header plus the file's own identity."""
    info = read_save_info(path)
    file_stat = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "filename": os.path.basename(path),
        "session_name": info.sessionName,
        "save_identifier": info.saveIdentifier,  # stable per WORLD, groups saves
        "save_header_version": info.saveHeaderType,
        "save_version": info.saveVersion,
        "build_version": info.buildVersion,
        "play_duration_s": info.playDurationInSeconds,
        "save_datetime_ticks": info.saveDateTimeInTicks,
        "is_modded": truthy(getattr(info, "isModdedSave", False)),
        "is_creative": truthy(getattr(info, "isCreativeModeEnabled", False)),
        "mtime_ns": file_stat.st_mtime_ns,
        "size": file_stat.st_size,
    }
