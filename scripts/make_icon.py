#!/usr/bin/env python3
"""
Generate icon.png for the MCPB extension.

Pure standard library, so the icon is reproducible from source without adding
an image dependency to a project that otherwise has none. Shapes are drawn
with signed distance functions and 4x supersampling, which gives clean edges
without a graphics library.

Run from the repository root:

    python3 scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZE = 512
SUPERSAMPLE = 4

# Deep slate-teal background, light drive bays, a green activity LED.
BG_TOP = (44, 83, 100)
BG_BOTTOM = (15, 32, 39)
BAY = (226, 235, 240)
BAY_SHADOW = (150, 170, 182)
LED = (74, 222, 128)


def rounded_rect_distance(px: float, py: float, cx: float, cy: float,
                          half_w: float, half_h: float, radius: float) -> float:
    """Signed distance to a rounded rectangle; negative means inside."""
    dx = abs(px - cx) - (half_w - radius)
    dy = abs(py - cy) - (half_h - radius)
    outside = (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def blend(base: tuple[int, int, int], layer: tuple[int, int, int],
          alpha: float) -> tuple[int, int, int]:
    return tuple(round(b + (l - b) * alpha) for b, l in zip(base, layer))


def sample(x: float, y: float) -> tuple[int, int, int, int]:
    """Colour and coverage for one sub-pixel."""
    body = rounded_rect_distance(x, y, SIZE / 2, SIZE / 2, SIZE / 2, SIZE / 2, 112)
    if body > 0:
        return (0, 0, 0, 0)

    # Vertical gradient across the badge.
    t = y / SIZE
    colour = blend(BG_TOP, BG_BOTTOM, t)

    # Three drive bays.
    bay_height = 58.0
    gap = 30.0
    total = 3 * bay_height + 2 * gap
    top = (SIZE - total) / 2
    for index in range(3):
        centre_y = top + index * (bay_height + gap) + bay_height / 2
        d = rounded_rect_distance(x, y, SIZE / 2, centre_y, 158, bay_height / 2, 16)
        if d < 0:
            colour = BAY
            # A thin darker lip along the bottom edge gives the bay some depth.
            if d > -6:
                colour = blend(BAY, BAY_SHADOW, 0.55)
            # Activity LED on the right of each bay.
            led = ((x - 396) ** 2 + (y - centre_y) ** 2) ** 0.5 - 11
            if led < 0:
                colour = LED
            break

    return (colour[0], colour[1], colour[2], 255)


def render() -> bytes:
    """Render the icon as raw RGBA scanlines with a PNG filter byte per row."""
    step = 1.0 / SUPERSAMPLE
    offset = step / 2
    weight = 1.0 / (SUPERSAMPLE * SUPERSAMPLE)
    raw = bytearray()

    for py in range(SIZE):
        raw.append(0)  # filter type: none
        for px in range(SIZE):
            r = g = b = a = 0.0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    sr, sg, sb, sa = sample(px + sx * step + offset,
                                            py + sy * step + offset)
                    coverage = sa / 255.0
                    r += sr * coverage
                    g += sg * coverage
                    b += sb * coverage
                    a += sa
            if a > 0:
                # Un-premultiply so edge pixels keep their colour.
                total_coverage = a / 255.0
                raw += bytes((round(r / total_coverage), round(g / total_coverage),
                              round(b / total_coverage), round(a * weight)))
            else:
                raw += b"\x00\x00\x00\x00"

    return bytes(raw)


def chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def write_png(path: Path, raw: bytes) -> None:
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("icon.png")
    write_png(target, render())
    print(f"wrote {target} ({target.stat().st_size} bytes, {SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
