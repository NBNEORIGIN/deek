"""Generate a brand-letter icon set (R for Rex, etc).

Usage:
    python _generate_icons_letter.py R brand/rex
    python _generate_icons_letter.py D brand/deek

Outputs the standard PWA icon set into the chosen subdirectory:
    icon.svg
    icon-192.png
    icon-512.png
    icon-maskable-512.png
    apple-touch-icon.png
    favicon.png
    favicon.ico

Design: emerald-700 circle, white serif letter centered. Maskable
variant has 20% safe-zone inset.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BG = (4, 120, 87, 255)          # emerald-700
LETTER = (255, 255, 255, 255)
MASTER = 1024


def _find_font(preferred: list[str]) -> str | None:
    candidates = [
        # Windows
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        # Mac
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVu-Serif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ]
    for c in preferred + candidates:
        if c and os.path.exists(c):
            return c
    return None


def _render_master(letter: str) -> Image.Image:
    """Render the full-size master icon — circle + centered letter."""
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, MASTER - 1, MASTER - 1), fill=BG)

    font_path = _find_font([])
    # Pick a size that visually fills ~60% of the icon. Iteratively
    # measure with the chosen font.
    target = int(MASTER * 0.65)
    size = target
    font = (
        ImageFont.truetype(font_path, size) if font_path
        else ImageFont.load_default()
    )
    while True:
        bbox = draw.textbbox((0, 0), letter, font=font, anchor="lt")
        h = bbox[3] - bbox[1]
        if h <= target or size <= 32:
            break
        size = int(size * target / h)
        font = (
            ImageFont.truetype(font_path, size) if font_path
            else ImageFont.load_default()
        )

    # Center the glyph using anchor="mm"
    draw.text((MASTER // 2, MASTER // 2), letter, fill=LETTER, font=font, anchor="mm")
    return img


def _save_png(img: Image.Image, size: int, path: Path) -> None:
    resized = img.resize((size, size), Image.LANCZOS)
    resized.save(path, format="PNG", optimize=True)


def _save_maskable(img: Image.Image, size: int, path: Path) -> None:
    """Adaptive icon — same artwork inside an 80% safe zone, full bg."""
    inner = int(size * 0.80)
    canvas = Image.new("RGBA", (size, size), BG)
    artwork = img.resize((inner, inner), Image.LANCZOS)
    # Strip transparent edge so artwork sits on the bg cleanly
    canvas.paste(artwork, ((size - inner) // 2, (size - inner) // 2), artwork)
    canvas.save(path, format="PNG", optimize=True)


def _save_svg(letter: str, path: Path) -> None:
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <circle cx="512" cy="512" r="512" fill="rgb(4,120,87)"/>
  <text x="512" y="540" text-anchor="middle"
        font-family="Georgia, 'Times New Roman', serif"
        font-size="640" font-weight="bold" fill="white"
        dominant-baseline="middle">{letter}</text>
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def main():
    if len(sys.argv) < 3:
        print("usage: python _generate_icons_letter.py <letter> <out_subdir>", file=sys.stderr)
        sys.exit(2)
    letter = sys.argv[1].strip()[:1].upper()
    if not letter:
        print("letter required", file=sys.stderr)
        sys.exit(2)
    out_dir = Path(__file__).parent / sys.argv[2]
    out_dir.mkdir(parents=True, exist_ok=True)

    master = _render_master(letter)

    # Standard PNG variants
    _save_png(master, 192, out_dir / "icon-192.png")
    _save_png(master, 512, out_dir / "icon-512.png")
    _save_png(master, 180, out_dir / "apple-touch-icon.png")
    _save_png(master, 32, out_dir / "favicon.png")

    # Maskable
    _save_maskable(master, 512, out_dir / "icon-maskable-512.png")

    # ICO (multi-size)
    favicon_sizes = [(16, 16), (32, 32), (48, 48)]
    favicon_imgs = [master.resize(s, Image.LANCZOS) for s in favicon_sizes]
    favicon_imgs[0].save(
        out_dir / "favicon.ico",
        format="ICO",
        sizes=favicon_sizes,
    )

    # SVG
    _save_svg(letter, out_dir / "icon.svg")

    print(f"wrote {out_dir} for letter='{letter}'")


if __name__ == "__main__":
    main()
