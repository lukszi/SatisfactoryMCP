"""The artwork borrow lends relief light, not the drawing's outline and contour strokes.

tools/mapgen/README.md, "Light". A synthetic sheet.
"""

from __future__ import annotations

import numpy as np

from mapgen.lighting.hillshade import artwork_detail


def test_the_borrow_drops_thin_ink_and_keeps_broad_shading():
    sheet = np.full((256, 256, 3), 150, np.uint8)
    sheet[:, 60:62] = 30  # a two-pixel dark stroke
    sheet[:, 100:102] = 250  # and a light one
    sheet[100:160, 150:210] = 100  # a broad darker plate

    detail, meta = artwork_detail(sheet)

    assert np.abs(detail[100:160, 56:66].astype(int)).max() < 12
    assert np.abs(detail[100:160, 96:106].astype(int)).max() < 12
    assert detail[130, 152] < -40 and meta["ink_closing_px"] > 2
