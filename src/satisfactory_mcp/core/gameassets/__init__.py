"""Reading the installed game's own cooked assets: the container and what is inside it.

Everything here is *generation-time* code. Nothing the server answers a request with comes
through this package -- the artifacts under ``data/`` do, and these modules are what the
``tools/gen_*.py`` scripts use to cut them. It lives in ``core`` rather than in ``tools``
because four generators read the same container in the same way, and the alternative was
what actually existed: three of them importing a 3,700-line generator by file path to get
at its opening section.

Two rules hold this package together, and both are enforced by reading the source in
``tests/test_architecture.py`` rather than by intention:

* **The ``gen`` extra is optional at import time.** ``ooz``, ``texture2ddecoder`` and Pillow
  are pinned in ``[project.optional-dependencies] gen`` and are the only way to decompress
  an Oodle block or unpack a BC1 texture -- and no module here imports one at module scope.
  A machine with none of them installed imports every module in this package, runs the whole
  test suite, and serves the map; it just cannot *generate*. Same posture ``interfaces/web``
  has towards fastapi.
* **The decoders are injected, not found.** ``IoStore(paks, name, decompress)`` takes its
  block decompressor as a callable, ``textures.decode_bc1_rgba`` takes the BC1 decoder and
  Pillow, and ``pyramid.install_pyramid`` takes the image module and the sheet -- so the
  suite drives all three with stand-ins and there is no ``sys.path`` mutation, no
  ``importlib``, and no import of the extra anywhere but inside a function body.
  ``iostore.oodle_decompress`` is the one such body, and it is a convenience a caller may
  pass in -- not a dependency this package reaches for on its own.
  ``pyramid.encode_tile_row`` is the second, and it is the exception that proves the shape
  of the rule: a spawned worker cannot be handed a module object through a pickle, so it
  imports Pillow itself -- inside the function, in a process that only exists because a
  caller who already had Pillow asked for workers.
"""
