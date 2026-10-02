#!/usr/bin/env python3
"""make_app_icon.py — regenerate Ylos.app/Contents/Resources/AppIcon.icns.

Dev-time tool: needs Pillow (`pip install pillow`). Not imported by any DCC and not by the test
suite — the .icns it writes is committed, so nobody needs Pillow just to use the launcher.

    python3 tools/macos/make_app_icon.py                       # macOS 26+: full-bleed opaque square
    python3 tools/macos/make_app_icon.py --style classic       # macOS <= 15: squircle + margin + shadow
    python3 tools/macos/make_app_icon.py --preview icon.png    # also write the 1024 px master

Why two styles. macOS 26 ("Tahoe") masks a legacy .icns itself, but only when the image's edge
pixels are (almost) fully opaque — reported threshold alpha >= 253 (community finding, Apple
Developer Forums thread 797971, not Apple documentation). Anything else, e.g. the classic Big Sur
template with its transparent margin, is shrunk into a grey squircle frame. Older macOS draws the
image exactly as given, so a full-bleed square would show sharp corners there. The default is the
style that looks right on the current macOS; use --style classic on a Mac that is still on 15.

The glyph is a "Y" read as a pipeline: two inputs (hollow nodes) merge into one output (solid
node). Colours come from app.html's :root so the icon belongs to the cockpit.
"""
from __future__ import annotations

import argparse
import io
import math
import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")

RS = getattr(Image, "Resampling", Image)        # Pillow >= 9.1 spells the filters here

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "Ylos.app" / "Contents" / "Resources" / "AppIcon.icns"

SIZE = 1024          # master canvas
SS = 4               # supersampling of the glyph (anti-aliasing)

BG_LIGHT = (201, 79, 138)        # cockpit --accent-hover (#c2497f), lifted a touch
BG_DARK = (102, 22, 64)          # deeper magenta, same family as --wine-hi
GLYPH_TOP = (255, 247, 251)
GLYPH_BOTTOM = (244, 208, 226)   # ~ --on-wine (#f6d7e3)
SHADOW = (48, 8, 28)

# Glyph geometry on the 1024 canvas: centre lines of the strokes (round caps) and the nodes.
ARM_L, ARM_R, JOINT, FOOT = (338, 276), (686, 276), (512, 524), (512, 752)
STROKE, NODE_R, NODE_HOLE = 92, 66, 28

# (icns type, pixel size). PNG payloads, as iconutil writes them; 'icp*'/'ic11..14' are the
# 16/32-pt and @2x slots.
ICNS_SLOTS = (
    ("icp4", 16), ("icp5", 32), ("ic11", 32), ("icp6", 64), ("ic12", 64),
    ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512), ("ic10", 1024),
)


def _lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def background() -> Image.Image:
    n = 256
    grad = Image.new("RGB", (n, n))
    grad.putdata([_lerp(BG_LIGHT, BG_DARK, (x * 0.35 + y * 0.65) / (n - 1))
                  for y in range(n) for x in range(n)])
    img = grad.resize((SIZE, SIZE), RS.BICUBIC)
    # Soft highlight, top-left. radial_gradient() only reaches white at the CORNERS, so the
    # inverted ramp is still ~75/255 at the middle of each edge: cut it to exactly 0 before the
    # border of the source, otherwise the crop below ends on a visible step.
    g = int(SIZE * 1.5)
    glow = ImageChops.invert(Image.radial_gradient("L")).resize((g, g), RS.BICUBIC)
    left, top = g // 2 - int(SIZE * 0.30), g // 2 - int(SIZE * 0.16)
    glow = glow.crop((left, top, left + SIZE, top + SIZE)).point(lambda v: int(max(0, v - 90) * 0.40))
    return Image.composite(Image.new("RGB", (SIZE, SIZE), (255, 255, 255)), img, glow)


def glyph_mask() -> Image.Image:
    n, k = SIZE * SS, SS
    layer = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(layer)

    def disc(c, r, fill):
        x, y = c[0] * k, c[1] * k
        d.ellipse((x - r * k, y - r * k, x + r * k, y + r * k), fill=fill)

    for a, b in ((ARM_L, JOINT), (ARM_R, JOINT), (JOINT, FOOT)):
        d.line([(a[0] * k, a[1] * k), (b[0] * k, b[1] * k)], fill=255, width=STROKE * k)
    for c in (ARM_L, ARM_R, JOINT, FOOT):            # round caps and joint
        disc(c, STROKE / 2, 255)
    for c in (ARM_L, ARM_R, FOOT):                   # nodes
        disc(c, NODE_R, 255)
    for c in (ARM_L, ARM_R):                         # the two inputs are hollow, the output is solid
        disc(c, NODE_HOLE, 0)
    return layer.resize((SIZE, SIZE), RS.LANCZOS)


def render_art() -> Image.Image:
    """Opaque 1024x1024 artwork, no rounded corners: the system (or classic_from) shapes it."""
    art = background()
    mask = glyph_mask()
    shadow = mask.filter(ImageFilter.GaussianBlur(SIZE * 0.014)).point(lambda v: int(v * 0.40))
    shadow = ImageChops.offset(shadow, 0, int(SIZE * 0.016))
    art = Image.composite(Image.new("RGB", (SIZE, SIZE), SHADOW), art, shadow)
    tint = Image.new("RGB", (1, SIZE))
    y0, y1 = ARM_L[1] - NODE_R, FOOT[1] + NODE_R
    tint.putdata([_lerp(GLYPH_TOP, GLYPH_BOTTOM, (y - y0) / (y1 - y0)) for y in range(SIZE)])
    return Image.composite(tint.resize((SIZE, SIZE), RS.BILINEAR), art, mask)


def squircle_mask(size: int, n: float = 5.0, ss: int = 4) -> Image.Image:
    """Superellipse |x|^n + |y|^n = 1 (n=5 is a close cousin of Apple's continuous corner)."""
    big = size * ss
    pts = []
    for i in range(720):
        t = 2 * math.pi * i / 720
        c, s = math.cos(t), math.sin(t)
        x = math.copysign(abs(c) ** (2 / n), c)
        y = math.copysign(abs(s) ** (2 / n), s)
        pts.append((big / 2 + x * big / 2, big / 2 + y * big / 2))
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m.resize((size, size), RS.LANCZOS)


def classic_from(art: Image.Image) -> Image.Image:
    """Big Sur template: 824 px squircle centred on the 1024 canvas, soft drop shadow."""
    tile, off = 824, 100
    mask = squircle_mask(tile)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sh = Image.new("L", (SIZE, SIZE), 0)
    sh.paste(mask, (off, off + 12))
    sh = sh.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.30))
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.putalpha(sh)
    out = Image.alpha_composite(out, shadow)
    body = art.resize((tile, tile), RS.LANCZOS).convert("RGBA")
    body.putalpha(mask)
    out.alpha_composite(body, (off, off))
    return out


def icns_bytes(master: Image.Image) -> bytes:
    pngs: dict[int, bytes] = {}
    chunks = []
    for kind, px in ICNS_SLOTS:
        if px not in pngs:
            im = master if px == master.width else master.resize((px, px), RS.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            pngs[px] = buf.getvalue()
        chunks.append(kind.encode("ascii") + struct.pack(">I", 8 + len(pngs[px])) + pngs[px])
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--style", choices=("fullbleed", "classic"), default="fullbleed",
                    help="fullbleed = macOS 26+, classic = macOS <= 15 (default: %(default)s)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="default: %(default)s")
    ap.add_argument("--preview", type=Path, help="also write the 1024 px master as a PNG")
    args = ap.parse_args()

    art = render_art()
    if args.style == "fullbleed":
        master = art.convert("RGBA")
        # The whole point of this style: every pixel, edges included, fully opaque.
        assert master.getchannel("A").getextrema() == (255, 255)
    else:
        master = classic_from(art)

    data = icns_bytes(master)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    if args.preview:
        master.save(args.preview)
    print(f"[icon] {args.style}: {args.out} ({len(data) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
