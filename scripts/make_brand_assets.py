"""Generate the eight brand images from Triad's mark.

    python scripts/make_brand_assets.py

## Why these files, and why eight

A custom integration's icon comes from ``custom_components/<domain>/brand/`` -- since Home
Assistant 2026.3 the frontend fetches it through ``/api/brands/integration/{domain}/{image}``, and
a local ``brand/`` directory takes priority over the brands CDN. Nothing is submitted anywhere;
``home-assistant/brands`` explicitly refuses pull requests for custom components, so a 404 from
``brands.home-assistant.io`` is normal and means nothing.

Home Assistant asks for ``icon``, ``logo``, their ``@2x`` variants, and a ``dark_`` prefixed
version of each, choosing the ``dark_`` one on a dark theme. The ``dark_`` files are purely
additive -- ``dark_icon.png`` falls back to ``icon.png`` -- which is exactly what this integration
was relying on before, and why the icon rendered as a **black tile on a light theme**.

## What was wrong before

The previous version of this script *drew* an approximation of the mark onto a solid black
canvas. Two problems, and the second is the one that showed:

* It was an approximation. The real mark is right here; reproducing it by eye is work spent to be
  less accurate.
* **The background was opaque.** An integration icon is composited onto whatever card Home
  Assistant puts behind it, so a black tile reads as a placeholder on a light theme rather than as
  a logo.

## Keying, not drawing

Every pixel far enough from the artwork's own corner colour is ink; everything else becomes
transparent. The background colour is read from a corner rather than assumed.

**The knockout stays a knockout.** The inverted triangle at the centre of the mark is not painted
-- it is the page showing through -- so keying leaves it transparent and it takes the colour of
whatever sits behind it. Filling it black would be wrong on a light theme for the same reason the
old opaque background was.

## One source, two polarities

Triad publish the mark white-on-black only, so unlike an artwork published both ways the light
variant has to be made: the ink is recoloured, the geometry is untouched. White ink for a dark
theme, near-black for a light one, both on transparency.

## Resizing: coverage only

The sibling AVPro integration premultiplies by alpha before resizing, because its artwork carries
real colour that Lanczos would otherwise average against transparent black and fringe every glyph
in grey. **That does not apply here.** This mark is a single flat ink, so only *coverage* is
interpolated and the colour is painted through it afterwards -- there is no colour to bleed and no
premultiply/divide round trip to do.

The clamp still matters. Lanczos overshoots, and an alpha above 255 or below 0 wraps when cast.

Pillow and NumPy are development dependencies only. They are used here and nowhere in the
integration, and ``manifest.json`` stays at ``requirements: []``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "assets" / "triad.jpg"
BRAND_DIR = REPO_ROOT / "custom_components" / "triad_ams" / "brand"

#: How far a pixel must sit from the artwork's own background before it counts as ink, summed
#: across the channels. Comfortably above JPEG noise and far below the 765 that separates this
#: artwork's black ground from its white ink.
INK_DISTANCE = 90

#: Ink colour per variant. The empty prefix is the light theme, which needs dark ink; ``dark_`` is
#: the dark theme, which keeps the artwork's own white.
INK = {"": (17, 17, 17), "dark_": (255, 255, 255)}

#: Icon height, and the logo's. The brands specification asks for images "trimmed, so [they
#: contain] the minimum amount of empty space", so the logo's width follows its aspect ratio
#: rather than being padded to a fixed box.
ICON_SIZE = 256
LOGO_HEIGHT = 256


def keyed(path: Path) -> np.ndarray:
    """Load the artwork and return an alpha mask with the background removed."""
    rgb = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    background = rgb[0, 0]
    distance = np.abs(rgb - background).sum(axis=2)
    return np.where(distance > INK_DISTANCE, 255, 0).astype(np.uint8)


def bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    """Inclusive bounding box of everything opaque."""
    ys, xs = np.where(alpha > 0)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def wordmark_split(alpha: np.ndarray) -> int:
    """The row separating the triangle from the TRIAD wordmark.

    Found as the **widest** empty band rather than a fixed row: the mark's own horizontal bands
    leave narrow gaps too, and picking the first one would cut the triangle in half. Measured on
    this artwork the gaps between bands are 8 rows and this one is 25, so the two are not close.
    """
    left, top, right, bottom = bounds(alpha)
    rows = (alpha[:, left : right + 1] > 0).sum(axis=1)

    widest, start, best = 0, None, bottom
    for y in range(top, bottom + 1):
        if rows[y] == 0:
            start = y if start is None else start
        elif start is not None:
            if (span := y - start) > widest:
                widest, best = span, (start + y) // 2
            start = None
    return best


def resized(alpha: np.ndarray, box: tuple[int, int, int, int], size: tuple[int, int]) -> np.ndarray:
    """Crop a coverage mask and scale it, without letting Lanczos ring the edges.

    Only coverage is interpolated here -- the ink is a flat colour applied afterwards -- so there
    is no premultiply/divide round trip to do. The clamp still matters: Lanczos overshoots, and an
    alpha above 255 or below 0 would wrap when cast.
    """
    left, top, right, bottom = box
    crop = Image.fromarray(alpha[top : bottom + 1, left : right + 1], "L")
    scaled = np.array(crop.resize(size, Image.LANCZOS)).astype(np.float64)
    return np.clip(scaled, 0, 255).round().astype(np.uint8)


def inked(coverage: np.ndarray, colour: tuple[int, int, int]) -> Image.Image:
    """Paint a flat ink colour through a coverage mask onto transparency."""
    rgb = np.zeros((*coverage.shape, 3), dtype=np.uint8)
    rgb[:] = colour
    return Image.fromarray(np.dstack([rgb, coverage]), "RGBA")


def centred(art: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Place art on a transparent canvas of the given size."""
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(art, ((size[0] - art.width) // 2, (size[1] - art.height) // 2), art)
    return canvas


def main() -> None:
    if not SOURCE.exists():
        msg = f"{SOURCE} is missing; the brand images are generated from it"
        raise SystemExit(msg)

    alpha = keyed(SOURCE)
    split = wordmark_split(alpha)

    # The icon is the triangle alone; the logo is the triangle with the wordmark beneath it.
    mark_box = bounds(alpha[:split])
    logo_box = bounds(alpha)

    mark_w = mark_box[2] - mark_box[0] + 1
    mark_h = mark_box[3] - mark_box[1] + 1
    logo_w = logo_box[2] - logo_box[0] + 1
    logo_h = logo_box[3] - logo_box[1] + 1

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    written = []

    for prefix, colour in INK.items():
        for scale, suffix in ((1, ""), (2, "@2x")):
            # Icon: the mark scaled to fit a square, keeping its equilateral proportions.
            side = ICON_SIZE * scale
            icon_w = round(side * mark_w / mark_h) if mark_w < mark_h else side
            icon_h = side if mark_w < mark_h else round(side * mark_h / mark_w)
            art = inked(resized(alpha, mark_box, (icon_w, icon_h)), colour)
            path = BRAND_DIR / f"{prefix}icon{suffix}.png"
            centred(art, (side, side)).save(path, "PNG", optimize=True)
            written.append(path)

            # Logo: trimmed to its own aspect rather than padded into a box.
            height = LOGO_HEIGHT * scale
            width = round(height * logo_w / logo_h)
            art = inked(resized(alpha, logo_box, (width, height)), colour)
            path = BRAND_DIR / f"{prefix}logo{suffix}.png"
            art.save(path, "PNG", optimize=True)
            written.append(path)

    print(f"{SOURCE.name}: mark {mark_w}x{mark_h}, logo {logo_w}x{logo_h}, split row {split}")
    for path in sorted(written):
        with Image.open(path) as im:
            print(f"  {path.name:<18} {im.size!s:<12} {path.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
