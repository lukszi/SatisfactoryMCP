"""Reading the installed game's own cooked assets: the container and what is inside it.

The container readers are what the ``tools/`` generators cut the artifacts under ``data/``
with. The server itself reaches only ``versions``, ``provenance`` and ``pyramid``: the
artifact versions, which build an artifact or the install is, and the tile layout. The
optional ``gen`` extra, and how it stays optional at import time, is in DESIGN.md.
"""
