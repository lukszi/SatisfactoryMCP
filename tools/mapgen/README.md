# mapgen

The offline map generators: the heightfield, caves and rocks cut from the installed game,
the paint layers, the game's own map artwork, and the drawn renders served as map tiles.
It is a uv workspace member of the repository and is not part of the shipped wheel.

```
python -m mapgen heightmap | caves | rocks | paint | artwork | renders | check-fill
```

`tools/gen_*.py` keep their paths and hand their arguments to these commands, so the
server and every recorded sidecar still name them.

## Layout

| package | holds |
|---|---|
| `gamedata/` | frame, level sweep, mesh decode, paint layers, biome, caves, rocks |
| `terrain/` | fill, sampling kernels, geometry rasters, measurements |
| `palette/` | styles and `palettes/*.json`, painted ground, water, shore |
| `lighting/` | hillshade, sun term, artwork borrow; `lights/` for light files |
| `tiles/` | band loop, pyramid cut, sidecar |
| `pipeline.py`, `cache.py`, `cli.py` | renders orchestrator, raster caches, entry point |

## Tests

```
PYTHONPATH=src;tools/mapgen/src python -m pytest tools/mapgen/tests -p no:xdist
```
