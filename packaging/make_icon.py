# /// script
# dependencies = ["pillow"]
# ///
"""Draws the app icon (blue rounded square with a document): build/LocalOffice.iconset/*.png, plus LocalOffice.icns on a Mac."""
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
d.rounded_rectangle((100, 100, 924, 924), radius=190, fill=(47, 91, 211))
d.polygon([(330, 250), (590, 250), (700, 360), (700, 780), (330, 780)], fill="white")     # page
d.polygon([(590, 250), (700, 360), (590, 360)], fill=(190, 205, 245))                       # folded corner
for y in (450, 530, 610):
    d.rounded_rectangle((390, y, 640, y + 26), radius=13, fill=(47, 91, 211))                # text lines
for size in (16, 32, 128, 256, 512):
    img.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
    img.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
if sys.platform == "darwin":
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(BUILD / "LocalOffice.icns")], check=True)
print("icon:", iconset)
