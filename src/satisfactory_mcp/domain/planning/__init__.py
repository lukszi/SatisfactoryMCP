"""Turning a wish into a buildable plan: solve, read out, lay out, site and track.

``solver`` builds and solves the LP, ``readout`` reads a solution out, ``analysis`` asks
questions of many solves, ``layout`` and ``siting`` make it buildable and place it,
``progress`` matches it against the save, and ``stored`` keeps plans on disk.

Import-free on purpose, like the other domain packages: ``domain.world.state`` lazily
reaches back for ``PlanStore``, and a re-exporting ``__init__`` would make that a cycle.
Nothing here formats a response; the renderers are in ``presenters.text``.
"""
