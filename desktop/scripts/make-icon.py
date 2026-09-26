"""Draw the app icon source: a page being turned into Markdown.

Run from the desktop directory, then hand the result to `npx tauri icon`:

    python3 scripts/make-icon.py
    npx tauri icon src-tauri/icon-source.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUTPUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icon-source.png"


def rounded_gradient(size: int, radius: int, top: tuple, bottom: tuple) -> Image.Image:
    """A vertical gradient clipped to a rounded square."""
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        ratio = y / (size - 1)
        gradient.putpixel(
            (0, y),
            tuple(round(top[channel] + (bottom[channel] - top[channel]) * ratio) for channel in range(3)),
        )
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    return canvas


def draw_page(draw: ImageDraw.ImageDraw) -> None:
    """A white sheet with a folded corner and a few lines of text."""
    left, top, right, bottom = 250, 190, 700, 800
    fold = 130
    draw.polygon(
        [
            (left, top),
            (right - fold, top),
            (right, top + fold),
            (right, bottom),
            (left, bottom),
        ],
        fill=(255, 255, 255, 245),
    )
    # The folded corner, a shade darker.
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=(206, 214, 226, 255),
    )
    for index in range(5):
        y = top + 210 + index * 70
        width = (right - left) - 120 if index % 2 == 0 else (right - left) - 230
        draw.rounded_rectangle((left + 60, y, left + 60 + width, y + 26), radius=13,
                               fill=(150, 162, 180, 255))


def draw_arrow(draw: ImageDraw.ImageDraw) -> None:
    """A download arrow, overlapping the page's bottom-right corner."""
    centre_x, top_y = 700, 520
    shaft = 54
    draw.rounded_rectangle(
        (centre_x - shaft // 2, top_y, centre_x + shaft // 2, top_y + 170),
        radius=shaft // 2,
        fill=(255, 255, 255, 255),
    )
    draw.polygon(
        [(centre_x - 110, top_y + 150), (centre_x + 110, top_y + 150), (centre_x, top_y + 300)],
        fill=(255, 255, 255, 255),
    )


def main() -> None:
    image = rounded_gradient(SIZE, radius=232, top=(52, 130, 246), bottom=(29, 64, 175))
    draw = ImageDraw.Draw(image)
    draw_page(draw)
    draw_arrow(draw)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT)
    print(f"wrote {OUTPUT} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
