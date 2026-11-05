# /// script
# dependencies = ["pillow"]
# ///
"""Draws the app icon (the cited line: a page with one highlighted line flagged past its edge): build/LocalOffice.iconset/*.png and LocalOffice.ico,
plus LocalOffice.icns on a Mac."""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

BUILD = Path(sys.argv[1])
iconset = BUILD / "LocalOffice.iconset"
iconset.mkdir(parents=True, exist_ok=True)

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
BLUE, YELLOW, INK, FOLD = (47, 91, 211), (255, 210, 73), (10, 10, 10), (191, 205, 245)
d.rounded_rectangle((100, 100, 924, 924), radius=190, fill=BLUE)
d.polygon([(318, 232), (598, 232), (706, 340), (706, 792), (318, 792)], fill="white")    # page
d.polygon([(598, 232), (706, 340), (598, 340)], fill=FOLD)                                 # folded corner
d.rounded_rectangle((356, 492, 776, 570), radius=10, fill=YELLOW)                          # cited line, flag past the edge
for y, w, color in ((420, 220, BLUE), (514, 268, INK), (612, 250, BLUE), (690, 160, BLUE)):
    d.rounded_rectangle((378, y, 378 + w, y + 34), radius=17, fill=color)                  # text lines
for size in (16, 32, 128, 256, 512):
    img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
    img.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
img.save(BUILD / "LocalOffice.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
if sys.platform == "darwin":
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(BUILD / "LocalOffice.icns")], check=True)
print("icon:", iconset)
