"""Generate the HACS brand assets.

Committed as a script rather than only as PNGs so the artwork is reviewable and reproducible --
a binary in a repo is a thing nobody can diff, and regenerating it by hand six months later means
guessing at the sizes and colours.

Home Assistant's brands convention:
  icon.png     256x256   used in the integrations list
  icon@2x.png  512x512   the same at 2x
  logo.png     256x256   optional; the same mark here, since the product has no separate wordmark

The mark is a 4x4 routing matrix: input rows crossing output columns, with the crosspoints that
are "connected" filled. That is literally what the device does, and it reads at 32px where
anything more detailed would turn to mush.

Run:  python scripts/make_brand_assets.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "triad_ams" / "brand"

#: Home Assistant renders brand icons on both light and dark backgrounds, so the mark carries its
#: own dark ground rather than relying on the page behind it.
BACKGROUND = (24, 28, 36, 255)
GRID = (86, 96, 116, 255)
ACTIVE = (3, 169, 244, 255)  # Home Assistant's accent blue, so it sits with the rest of the UI

#: Crosspoints that read as "routed". Chosen so no row or column is empty and the diagonal is
#: broken -- a plain diagonal reads as a generic grid rather than as a switching matrix.
ROUTED = {(0, 1), (1, 3), (2, 0), (3, 2)}

SIZE = 256
LINES = 4


def draw_matrix(size: int) -> Image.Image:
    """Draw the mark at an arbitrary size, so 1x and 2x are the same geometry, not a resample."""
    scale = size / SIZE
    image = Image.new("RGBA", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)

    margin = 44 * scale
    span = size - 2 * margin
    step = span / (LINES - 1)
    line_width = max(1, round(6 * scale))
    dot_radius = 15 * scale

    for i in range(LINES):
        offset = margin + i * step
        draw.line([(margin, offset), (size - margin, offset)], fill=GRID, width=line_width)
        draw.line([(offset, margin), (offset, size - margin)], fill=GRID, width=line_width)

    for row, column in ROUTED:
        cx = margin + column * step
        cy = margin + row * step
        draw.ellipse(
            [cx - dot_radius, cy - dot_radius, cx + dot_radius, cy + dot_radius], fill=ACTIVE
        )
    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", SIZE), ("icon@2x.png", SIZE * 2), ("logo.png", SIZE)):
        path = OUT_DIR / name
        draw_matrix(size).save(path, "PNG", optimize=True)
        print(f"{path.relative_to(OUT_DIR.parents[2])}  {size}x{size}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
