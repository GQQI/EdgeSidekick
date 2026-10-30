"""Generate EdgeSidekick friendly small-robot app icons (PNG + multi-size ICO).

Usage (from repo root):
  .venv\\Scripts\\python scripts\\make-edgesidekick-icons.py
"""
from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DESK = ROOT / "desktop" / "build"
ICONS = DESK / "icons"
PUB = ROOT / "ui" / "public"


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def gradient_bg(size: int) -> Image.Image:
    """Blue rounded-square background matching favicon.svg (#5b97ff -> #1d4fc4)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    c0 = (91, 151, 255)
    c1 = (47, 111, 237)
    c2 = (29, 79, 196)
    denom = 2 * (size - 1) if size > 1 else 1
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            if t < 0.55:
                u = t / 0.55
                r = int(lerp(c0[0], c1[0], u))
                g = int(lerp(c0[1], c1[1], u))
                b = int(lerp(c0[2], c1[2], u))
            else:
                u = (t - 0.55) / 0.45
                r = int(lerp(c1[0], c2[0], u))
                g = int(lerp(c1[1], c2[1], u))
                b = int(lerp(c1[2], c2[2], u))
            px[x, y] = (r, g, b, 255)

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    radius = max(2, int(round(size * 15 / 64)))
    md.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def draw_robot(size: int) -> Image.Image:
    """Friendly small robot: antenna + white head + two blue eyes."""
    img = gradient_bg(size)
    d = ImageDraw.Draw(img)
    s = size / 64.0

    def xy(*vals: float):
        return tuple(int(round(v * s)) for v in vals)

    blue = (47, 111, 237, 255)
    white = (255, 255, 255, 255)

    stroke = max(2, int(round(4.4 * s)))
    d.line([xy(32, 21), xy(32, 15)], fill=white, width=stroke)
    ar = max(2, int(round(4.8 * s)))
    cx, cy = xy(32, 12)
    d.ellipse((cx - ar, cy - ar, cx + ar, cy + ar), fill=white)

    head = xy(18, 21, 18 + 28, 21 + 31)
    hr = max(2, int(round(9.5 * s)))
    d.rounded_rectangle(head, radius=hr, fill=white)

    er = max(2, int(round(4.6 * s)))
    for ex, ey in ((26.5, 37), (37.5, 37)):
        ecx, ecy = xy(ex, ey)
        d.ellipse((ecx - er, ecy - er, ecx + er, ecy + er), fill=blue)

    if size >= 48:
        mx0, my = xy(27, 46)
        mx1, _ = xy(37, 46)
        smile_w = max(1, int(round(2.2 * s)))
        d.arc(
            (mx0, my - int(3 * s), mx1, my + int(4 * s)),
            start=20,
            end=160,
            fill=blue,
            width=smile_w,
        )

    return img


def write_png(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    draw_robot(size).save(path, format="PNG")
    print(f"  wrote {path.relative_to(ROOT)} ({size}x{size})")


def write_ico(path: Path, sizes: list[int]) -> None:
    """Write a multi-resolution ICO with PNG-compressed entries (Vista+)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    images = {s: draw_robot(s).convert("RGBA") for s in sizes}
    entries = []
    blobs = []
    for size in sizes:
        bio = BytesIO()
        images[size].save(bio, format="PNG")
        blob = bio.getvalue()
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries.append((w, h, len(blob)))
        blobs.append(blob)

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    for (w, h, nbytes), _blob in zip(entries, blobs):
        directory += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, nbytes, offset)
        offset += nbytes
    path.write_bytes(header + directory + b"".join(blobs))
    print(f"  wrote {path.relative_to(ROOT)} sizes={sizes} bytes={path.stat().st_size}")


def main() -> None:
    DESK.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    PUB.mkdir(parents=True, exist_ok=True)

    write_png(DESK / "icon.png", 512)
    write_png(ICONS / "icon.png", 512)
    for s in (512, 256, 128, 64, 48, 32, 24, 22, 16):
        write_png(ICONS / f"{s}x{s}.png", s)

    write_png(DESK / "tray.png", 32)
    write_png(DESK / "tray-16.png", 16)
    write_png(PUB / "favicon.png", 32)
    write_png(PUB / "brand-icon.png", 256)
    write_ico(DESK / "icon.ico", [16, 32, 48, 256])
    print("done")


if __name__ == "__main__":
    main()
