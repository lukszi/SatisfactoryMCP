"""The compact TSV presenter: primitives, plus one formatter module per concept.

Context budget is the binding constraint on every response here -- see
``primitives`` for the rules that follow from it.

Deliberately empty of imports, and it has to stay that way. Importing a submodule
runs this file first, so re-exporting the formatters here would drag the whole
planning package in behind every ``primitives.num`` call -- and the tool modules
reach for ``primitives`` on every response. ``primitives`` is imported under the
local alias ``render`` everywhere.
"""
