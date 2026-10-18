"""Render an original YouTube thumbnail locally; no external assets."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime" / "gauri-video" / "fallout-youtube-thumbnail.png"
SCALE = 2
image = Image.new("RGB", (1280 * SCALE, 720 * SCALE), "#101915")
draw = ImageDraw.Draw(image)


def rect(box, fill, radius=0, outline=None, width=1):
    draw.rounded_rectangle(tuple(int(v * SCALE) for v in box),
                           radius=radius * SCALE, fill=fill, outline=outline,
                           width=width * SCALE)


def line(points, fill, width=2):
    draw.line([(int(x * SCALE), int(y * SCALE)) for x, y in points],
              fill=fill, width=width * SCALE)


def text(x, y, value, size, color, bold=True):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/" + name,
                             size * SCALE)
    draw.text((x * SCALE, y * SCALE), value, font=font, fill=color,
              stroke_width=0)


for x in range(0, 1280, 48):
    line([(x, 0), (x, 720)], "#17241d", 1)
for y in range(0, 720, 48):
    line([(0, y), (1280, y)], "#17241d", 1)

rect((0, 0, 14, 720), "#8aefb4")
rect((48, 44, 264, 98), "#8aefb4", 10)
text(65, 49, "FALLOUT", 36, "#101915")
text(48, 155, "REPAIR", 91, "#f3f7f4")
text(48, 249, "DONE.", 104, "#8aefb4")
text(48, 379, "MEMORY", 86, "#f3f7f4")
text(48, 470, "WRONG.", 100, "#ff947d")
text(51, 619, "AI MEMORY REPAIR", 27, "#c2d1c8")

# Illustrative graphic, not a fabricated screenshot or real verifier report.
rect((670, 110, 1230, 568), "#1b2922", 26, "#3a5243", 2)
text(702, 137, "THE STALE CLAIM", 24, "#becfc4")
rect((703, 190, 1197, 329), "#302622", 14, "#936457", 2)
text(727, 205, "20% DISCOUNT", 42, "#ffffff")
text(730, 268, "Offer withdrawn. Still remembered.", 23, "#ffbaa9", False)

line([(743, 353), (743, 386), (927, 386)], "#749784", 3)
line([(927, 386), (918, 379)], "#749784", 3)
line([(927, 386), (918, 393)], "#749784", 3)
text(959, 366, "CHECK", 24, "#c2d1c8")

rect((703, 425, 923, 526), "#203e2b", 14)
text(723, 441, "REPAIR", 21, "#badbc6")
text(723, 470, "DONE", 39, "#8aefb4")
rect((942, 425, 1197, 526), "#4c2b26", 14)
text(963, 441, "VERIFIER", 21, "#ffd1c7")
text(963, 470, "FAIL", 39, "#ff947d")

rect((703, 597, 1016, 650), "#20382a", 12, "#527c60", 1)
text(724, 608, "Built with FalkorDB", 27, "#b7f4cc")
text(1036, 613, "DEMO", 23, "#becfc4")

OUT.parent.mkdir(parents=True, exist_ok=True)
image = image.resize((1280, 720), Image.Resampling.LANCZOS)
image.save(OUT, optimize=True)
with Image.open(OUT) as check:
    check.verify()
assert OUT.stat().st_size < 2 * 1024 * 1024
print("Created:", OUT, "— 1280 × 720 PNG;", OUT.stat().st_size, "bytes")