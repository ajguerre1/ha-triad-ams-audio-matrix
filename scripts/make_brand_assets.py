"""Generate the HACS brand assets from Triad's mark.

Committed as a script rather than only as PNGs so the artwork is reviewable and reproducible -- a
binary in a repo is a thing nobody can diff, and regenerating one by hand later means guessing at
the sizes and colours.

## The mark

Triad's logo is a Sierpinski triangle at one level of recursion, drawn in the negative:

    * the top sub-triangle is solid
    * the two lower sub-triangles are rendered as horizontal bands
    * the central inverted sub-triangle is background, so it reads as a wedge narrowing to a point
      at the bottom centre

Everything is derived from the outer triangle's three corners, so the proportions hold at any
size. Each asset is *drawn* at its own size rather than resampled from one master -- the bands are
thin enough that a downscale turns them to grey mush.

## Assets

Home Assistant's brands convention:

    icon.png     256x256   the mark alone -- what the integrations list shows
    icon@2x.png  512x512
    logo.png     512x256   mark plus the TRIAD wordmark
    logo@2x.png  1024x512

The wordmark needs a font, and fonts are not portable. A list of candidates is tried in order and
the first that exists wins; if none do, the logo is skipped rather than rendered in PIL's bitmap
default, which would look nothing like the original. The icons never need a font, so the assets
that matter always generate.

Run:  python scripts/make_brand_assets.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "custom_components" / "triad_ams" / "brand"

#: The mark is white on black in Triad's own artwork, and a dark ground keeps it legible on both
#: light and dark Home Assistant themes rather than vanishing into one of them.
BACKGROUND = (0, 0, 0, 255)
FOREGROUND = (255, 255, 255, 255)

#: Horizontal bands filling each lower sub-triangle. Five reads as banded at 256px and still
#: resolves at the 32px the integrations list uses.
BANDS = 5

#: Fraction of each band's slot that is drawn, leaving the rest as the gap.
BAND_DUTY = 0.62

#: Tried in order; the first that exists is used for the wordmark.
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def draw_mark(draw: ImageDraw.ImageDraw, apex, left, right) -> None:
    """Draw the Triad triangle given the outer corners.

    The three sub-triangles come from the edge midpoints, which is what makes this a Sierpinski
    subdivision rather than three shapes that happen to sit near each other.
    """
    mid_left = _lerp(apex, left, 0.5)  # midpoint of the left edge
    mid_right = _lerp(apex, right, 0.5)  # midpoint of the right edge
    mid_base = _lerp(left, right, 0.5)  # midpoint of the base

    # Top sub-triangle: solid.
    draw.polygon([apex, mid_left, mid_right], fill=FOREGROUND)

    # Lower sub-triangles: horizontal bands. Each band is a trapezium clipped to the sub-triangle,
    # so the left and right edges follow the slope instead of being cut square.
    for corner, inner_top, inner_bottom in (
        (left, mid_left, mid_base),  # bottom-left sub-triangle
        (right, mid_right, mid_base),  # bottom-right sub-triangle
    ):
        for band in range(BANDS):
            top_t = band / BANDS
            bottom_t = (band + BAND_DUTY) / BANDS
            # Outer edge runs mid -> corner; inner edge runs mid -> base midpoint.
            outer_top = _lerp(inner_top, corner, top_t)
            outer_bottom = _lerp(inner_top, corner, bottom_t)
            inner_top_pt = _lerp(inner_top, inner_bottom, top_t)
            inner_bottom_pt = _lerp(inner_top, inner_bottom, bottom_t)
            draw.polygon([outer_top, inner_top_pt, inner_bottom_pt, outer_bottom], fill=FOREGROUND)


def render_icon(size: int) -> Image.Image:
    """The mark alone, on a square canvas."""
    image = Image.new("RGBA", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)

    margin = size * 0.10
    width = size - 2 * margin
    # Equilateral: height is width * sqrt(3)/2, centred vertically.
    height = width * 0.866
    top = (size - height) / 2

    draw_mark(
        draw,
        apex=(size / 2, top),
        left=(margin, top + height),
        right=(size - margin, top + height),
    )
    return image


def _load_font(pixel_size: int) -> ImageFont.FreeTypeFont | None:
    for candidate in FONT_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return ImageFont.truetype(candidate, pixel_size)
    return None


def render_logo(width: int, height: int) -> Image.Image | None:
    """The mark above a letterspaced TRIAD wordmark, as in the original artwork."""
    font = _load_font(round(height * 0.15))
    if font is None:
        return None

    image = Image.new("RGBA", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    mark_height = height * 0.60
    mark_width = mark_height / 0.866
    top = height * 0.10
    draw_mark(
        draw,
        apex=(width / 2, top),
        left=(width / 2 - mark_width / 2, top + mark_height),
        right=(width / 2 + mark_width / 2, top + mark_height),
    )

    # Letterspacing is what makes the wordmark read as Triad's rather than as plain bold caps,
    # so the glyphs are placed individually instead of drawn as one string.
    letters = "TRIAD"
    tracking = height * 0.055
    widths = [draw.textlength(ch, font=font) for ch in letters]
    total = sum(widths) + tracking * (len(letters) - 1)
    x = (width - total) / 2
    y = top + mark_height + height * 0.10
    for ch, w in zip(letters, widths, strict=True):
        draw.text((x, y), ch, font=font, fill=FOREGROUND)
        x += w + tracking
    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []

    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        path = OUT_DIR / name
        render_icon(size).save(path, "PNG", optimize=True)
        written.append((name, f"{size}x{size}", path.stat().st_size))

    for name, (w, h) in (("logo.png", (512, 256)), ("logo@2x.png", (1024, 512))):
        image = render_logo(w, h)
        path = OUT_DIR / name
        if image is None:
            print(f"skipped {name}: no usable font found on this machine")
            continue
        image.save(path, "PNG", optimize=True)
        written.append((name, f"{w}x{h}", path.stat().st_size))

    for name, dims, size in written:
        print(f"  {name:<14} {dims:<10} {size:>6} bytes")


if __name__ == "__main__":
    main()
