#!/usr/bin/env python3
"""Generate the Information Health favicons from the brand mark — a white ECG
pulse on a rounded square in the brand primary color.

Single source of truth:
  * shape  → web/components/layout/logo.tsx  (the pulse path)
  * color  → web/app/globals.css  --primary  (HSL 245 58% 51%, light theme)

Pure standard library (zlib + struct): no Pillow/sharp/ImageMagick, no network —
so it runs anywhere and the icons are reproducible. Re-run after a brand change:

    python3 web/scripts/generate-favicons.py

Outputs (into web/public/): favicon.ico, favicon-16x16.png, favicon-32x32.png,
apple-touch-icon.png, android-chrome-192x192.png, android-chrome-512x512.png,
icon.svg, site.webmanifest.
"""
import math
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.normpath(os.path.join(HERE, "..", "public"))

# ---- brand -----------------------------------------------------------------
def hsl_to_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    hp = (h / 60.0) % 6
    x = c * (1 - abs(hp % 2 - 1))
    r, g, b = [
        (c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)
    ][int(hp)]
    m = l - c / 2
    return tuple(max(0, min(255, round((v + m) * 255))) for v in (r, g, b))

PRIMARY = hsl_to_rgb(245, 0.58, 0.51)          # --primary (light)
PRIMARY_HEX = "#%02x%02x%02x" % PRIMARY
WHITE = (255, 255, 255)

# Pulse polyline in the logo's 24x24 viewBox: M3 12 h3.5 l2 -5 l3 10 l2.5 -7 l1.5 2 H21
PULSE = [(3, 12), (6.5, 12), (8.5, 7), (11.5, 17), (14, 10), (15.5, 12), (21, 12)]

# ---- geometry --------------------------------------------------------------
def _dist_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    d2 = dx * dx + dy * dy
    if d2 == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / d2
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def render(size, rounded=True, opaque=False):
    """Return `size`x`size` RGBA bytes. Supersample a hard-edged master, then
    box-average down for anti-aliasing (round caps/joins fall out of the
    distance-to-polyline test)."""
    ss = max(1, round(512 / size))
    if size >= 256:
        ss = max(ss, 2)
    while size * ss > 1024:
        ss -= 1
    M = size * ss
    cx = cy = M / 2.0
    half = M / 2.0
    radius = 0.28 * M                      # rounded-tile corner radius
    scale = 0.80 * M / 24.0                # map the 24-box onto 80% of the tile
    hw = 0.045 * M                         # pulse stroke half-width (~9% of tile)
    pmap = [(cx + (x - 12) * scale, cy + (y - 12) * scale) for x, y in PULSE]
    xs = [p[0] for p in pmap]; ys = [p[1] for p in pmap]
    bx0, bx1 = min(xs) - hw, max(xs) + hw
    by0, by1 = min(ys) - hw, max(ys) + hw

    tile = [0.0] * (size * size)
    pulse = [0.0] * (size * size)
    inv = 1.0 / (ss * ss)
    for my in range(M):
        py = my + 0.5
        ty = (my // ss) * size
        row_pulse = by0 <= py <= by1
        for mx in range(M):
            px = mx + 0.5
            if opaque:
                inside = True
            elif rounded:
                qx = abs(px - cx) - (half - radius)
                qy = abs(py - cy) - (half - radius)
                d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
                inside = d <= 0
            else:
                inside = True
            if not inside:
                continue
            idx = ty + (mx // ss)
            tile[idx] += inv
            if row_pulse and bx0 <= px <= bx1:
                dd = min(_dist_seg(px, py, pmap[i][0], pmap[i][1],
                                   pmap[i + 1][0], pmap[i + 1][1])
                         for i in range(len(pmap) - 1))
                if dd <= hw:
                    pulse[idx] += inv

    pr, pg, pb = PRIMARY
    out = bytearray(size * size * 4)
    for i in range(size * size):
        ta = tile[i]
        pa = pulse[i]
        if pa > ta:
            pa = ta
        k = (pa / ta) if ta > 0 else 0.0   # white-over-primary coverage
        o = i * 4
        out[o] = round(pr * (1 - k) + 255 * k)
        out[o + 1] = round(pg * (1 - k) + 255 * k)
        out[o + 2] = round(pb * (1 - k) + 255 * k)
        out[o + 3] = 255 if opaque else round(ta * 255)
    return bytes(out)

# ---- encoders --------------------------------------------------------------
def _chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

def png_bytes(size, rgba):
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    stride = size * 4
    raw = bytearray()
    for y in range(size):
        raw.append(0)                      # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))

def ico_bytes(entries):                    # entries: [(size, png_bytes), ...]
    n = len(entries)
    out = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    dirs, datas = b"", b""
    for size, png in entries:
        w = 0 if size >= 256 else size
        dirs += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        datas += png
    return out + dirs + datas

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Information Health">
  <rect width="32" height="32" rx="9" fill="{primary}"/>
  <path d="M3 12h3.5l2-5 3 10 2.5-7 1.5 2H21" transform="translate(3.2 3.2) scale(1.0667)"
        fill="none" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

WEBMANIFEST = """{{
  "name": "Information Health",
  "short_name": "InfoHealth",
  "icons": [
    {{ "src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png" }},
    {{ "src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png" }}
  ],
  "theme_color": "{primary}",
  "background_color": "#ffffff",
  "display": "standalone"
}}
"""

def main():
    os.makedirs(PUBLIC, exist_ok=True)
    print("brand primary:", PRIMARY_HEX, PRIMARY)

    def emit_png(name, size, **kw):
        data = png_bytes(size, render(size, **kw))
        with open(os.path.join(PUBLIC, name), "wb") as f:
            f.write(data)
        print(f"  {name:32} {len(data):>7} bytes ({size}px)")
        return size, data

    print("rendering (pure-python, supersampled)…")
    s16 = emit_png("favicon-16x16.png", 16)
    s32 = emit_png("favicon-32x32.png", 32)
    s48 = (48, png_bytes(48, render(48)))                       # for the .ico only
    emit_png("apple-touch-icon.png", 180, rounded=False, opaque=True)
    emit_png("android-chrome-192x192.png", 192)
    emit_png("android-chrome-512x512.png", 512)

    ico = ico_bytes([s16, s32, s48])
    with open(os.path.join(PUBLIC, "favicon.ico"), "wb") as f:
        f.write(ico)
    print(f"  {'favicon.ico':32} {len(ico):>7} bytes (16+32+48 PNG-in-ICO)")

    with open(os.path.join(PUBLIC, "icon.svg"), "w") as f:
        f.write(SVG.format(primary=PRIMARY_HEX))
    with open(os.path.join(PUBLIC, "site.webmanifest"), "w") as f:
        f.write(WEBMANIFEST.format(primary=PRIMARY_HEX))
    print("  icon.svg + site.webmanifest written")
    print("done →", PUBLIC)

if __name__ == "__main__":
    main()
