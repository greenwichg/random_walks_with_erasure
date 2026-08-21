#!/usr/bin/env python3
"""Generate public/icon-maskable-512.png from icon.svg's geometry.

    python3 scripts/build-maskable-icon.py

Not committed-by-hand artwork: the glyph coordinates are lifted from `public/icon.svg` and the
transform is applied here, so the maskable icon cannot drift from the favicon it is derived from.
Re-run it if `icon.svg` changes.

Two things make a maskable icon different from a normal one, and both are easy to get wrong:

* **Full bleed.** No rounded corners. The OS supplies the shape — a circle on Pixel, a squircle on
  Samsung, a rounded square elsewhere — by masking whatever we provide. Ship a rounded icon and it
  gets rounded twice, with the background showing through the gap.
* **The safe zone.** Only the central 80% (a circle of radius 0.4x the width) is guaranteed to
  survive the mask. Anything outside it may be cropped on some device. The script prints the glyph's
  measured extent against that radius and says which side of it the result falls on.

Screenshots (`screenshot-wide.png` / `screenshot-narrow.png`) are NOT generated here — they are real
captures of the running app. To refresh them, drop a temporary spec into `e2e/specs/` that sets the
viewport, goes to `/stories`, and calls `page.screenshot({ path: "public/screenshot-wide.png" })`,
run `npx playwright test` on it (the config starts both servers), then delete the spec.
"""
from PIL import Image, ImageDraw

# The path from icon.svg, with its transform="translate(3.2 3.2) scale(1.0667)" applied.
RAW = [(3, 12), (6.5, 12), (8.5, 7), (11.5, 17), (14, 10), (15.5, 12), (21, 12)]
PTS32 = [(x * 1.0667 + 3.2, y * 1.0667 + 3.2) for x, y in RAW]
STROKE32 = 2.6 * 1.0667
BG, FG = (0x46, 0x3A, 0xCB, 255), (255, 255, 255, 255)

SIZE, SS = 512, 4          # output size; supersample factor for antialiasing
SHRINK = 0.85              # extra margin inside the 80% safe zone, for masks tighter than a circle
N = SIZE * SS

img = Image.new("RGBA", (N, N), BG)        # full bleed — the mask supplies the shape
draw = ImageDraw.Draw(img)


def to_px(p):
    return ((p[0] - 16) * (N / 32) * SHRINK + N / 2, (p[1] - 16) * (N / 32) * SHRINK + N / 2)


pts = [to_px(p) for p in PTS32]
width = STROKE32 * (N / 32) * SHRINK
draw.line(pts, fill=FG, width=int(round(width)), joint="curve")
for cx, cy in (pts[0], pts[-1]):            # round caps: Pillow's line() has none
    r = width / 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=FG)

img.resize((SIZE, SIZE), Image.LANCZOS).save("public/icon-maskable-512.png", optimize=True)

extent = max(((x - N / 2) ** 2 + (y - N / 2) ** 2) ** 0.5 for x, y in pts) + width / 2
print(f"glyph extent {extent / SS:.1f}px vs safe radius {0.4 * SIZE:.1f}px — "
      f"{'inside' if extent / SS <= 0.4 * SIZE else 'OUTSIDE, would be cropped'}")
