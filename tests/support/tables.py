"""Reading the tab-separated tables the MCP tools print."""

from __future__ import annotations

#: Lines a tool prints around its table: ``#`` notes and ``!`` warnings.
NOTE_PREFIXES = ("#", "!")


def table_lines(text: str, header: str | None = None) -> list[str]:
    """The table in a tool reply, header line first.

    Without ``header``: every tab-separated line that is not a note. With it: the lines
    from the one starting with ``header`` up to the first blank line or note, for a reply
    that prints more than one table.
    """
    lines = text.splitlines()
    if header is None:
        return [line for line in lines if "\t" in line and not line.startswith(NOTE_PREFIXES)]
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    table = [lines[start]]
    for line in lines[start + 1 :]:
        if not line or line.startswith(NOTE_PREFIXES):
            break
        table.append(line)
    return table


def data_lines(text: str, header: str | None = None) -> list[str]:
    """The table's rows below its header, unsplit."""
    return table_lines(text, header)[1:]


def rows(text: str, header: str | None = None) -> list[list[str]]:
    """The table's rows below its header, split into cells."""
    return [line.split("\t") for line in data_lines(text, header)]


def rows_by_header(text: str, header: str | None = None) -> list[dict[str, str]]:
    """The table's rows as dicts keyed by column name.

    By name rather than position, so a column added to the tool does not shift every
    later read onto the wrong field.
    """
    lines = table_lines(text, header)
    if not lines:
        return []
    names = lines[0].split("\t")
    return [dict(zip(names, line.split("\t"), strict=False)) for line in lines[1:]]
