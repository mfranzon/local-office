"""The Desk's presentations: a list of slides, each a title, a body in Markdown (short paragraphs, lists, tables)
and speaker notes; a slide can also have a background colour and one image, shown next to its text. The images
are files of their own, named by what they contain. A PowerPoint file is written from the slides on the built-in
template, widened to 16:9.
"""
import hashlib
import io
import math
import re
import time

from PIL import Image, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn
from pptx.util import Pt

from . import tools
from .config import DATA_DIR

IMAGES = DATA_DIR / "desk_images"
IMAGE_SIDE = 1920          # a larger image is made smaller: it fills a slide on a screen anyway
MAX_SLIDES = 300
PARTS = ("title", "body", "notes")
SIZES = (32, 28, 24, 20, 18, 16, 14, 12)   # of the body text, in points: the largest that fits the slide is used
TABLE_PT = 14


def colour(value) -> str:
    """A colour as #rrggbb, or "" if `value` isn't one written in hex."""
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", str(value or "").strip().lower())
    return "#" + (m[1] if len(m[1]) == 6 else "".join(c * 2 for c in m[1])) if m else ""


def dark(background: str) -> bool:
    """Whether text on this colour has to be white to be read."""
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b < 140


def is_image(name) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{16}\.(png|jpg)", str(name)))


def keep_image(data: bytes) -> str:
    """Stores a picture for the slides, as PNG if parts of it are transparent and JPEG otherwise; returns its name."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        clear = img.mode in ("RGBA", "LA") or "transparency" in img.info
        img = img.convert("RGBA" if clear else "RGB")
    except Exception:
        raise ValueError("this file is not a picture the Desk can show")
    img.thumbnail((IMAGE_SIDE, IMAGE_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG" if clear else "JPEG", **({} if clear else {"quality": 88}))
    name = hashlib.sha1(buf.getvalue()).hexdigest()[:16] + (".png" if clear else ".jpg")
    IMAGES.mkdir(parents=True, exist_ok=True)
    (IMAGES / name).write_bytes(buf.getvalue())
    return name


def sweep(used: set):
    """Removes the images no slide shows any more. One stored in the last hour may be on a slide not saved yet."""
    for path in IMAGES.glob("*") if IMAGES.is_dir() else ():
        if path.name not in used and path.stat().st_mtime < time.time() - 3600:
            path.unlink(missing_ok=True)


def clean(deck: list) -> list[dict]:
    """Slides as stored: every slide has its three parts as text, and the title is one line; a slide with a
    background colour or an image also says which."""
    out = []
    for s in deck:
        if isinstance(s, dict):
            slide = {k: str(s.get(k) or "").strip() for k in PARTS}
            look = {"background": colour(s.get("background")), "image": s.get("image") if is_image(s.get("image")) else ""}
            out.append(slide | {"title": " ".join(slide["title"].split())[:300]} | {k: v for k, v in look.items() if v})
    if len(out) > MAX_SLIDES:
        raise ValueError(f"a presentation on the Desk can have at most {MAX_SLIDES} slides")
    return out


def as_text(deck: list[dict], first: int = 1, last: int | None = None, cut: bool = False, whole: int | None = None) -> str:
    """Slides first..last as the assistant sees them, numbered. With `cut`, long bodies and notes are cut short
    (an outline of a long presentation), except those of slide `whole`."""
    def part(text: str, n: int) -> str:
        return text if not cut or n == whole or len(text) <= 200 else text[:160] + " … (cut: read_slides shows the rest)"
    return "\n\n".join(
        "\n".join(filter(None, [f"[{n}] Title: {s['title'] or '(none)'}", part(s["body"], n), s["notes"] and "Notes: " + part(s["notes"], n),
                                *(f"{k.capitalize()}: {s[k]}" for k in ("background", "image") if s.get(k))]))
        for n, s in enumerate(deck, 1) if first <= n <= (last or len(deck)))


# --- writing a PowerPoint file ---

def _lines(body: str) -> tuple[list[tuple[str, str, int]], list[list[list[str]]]]:
    """A body's (text, bullet | number | plain, level) lines, and its tables as rows of cells."""
    lines, tables, rows = [], [], []
    for line in body.splitlines():
        s = line.strip().removesuffix("\\")
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", s[1:-1])]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                rows.append(cells)
            continue
        if rows:
            tables.append(rows)
            rows = []
        level = min((len(line) - len(line.lstrip())) // 2, 4)
        if m := re.match(r"[-*+]\s+(.*)", s):
            lines.append((m[1], "bullet", level))
        elif m := re.match(r"\d+[.)]\s+(.*)", s):
            lines.append((m[1], "number", level))
        elif s and not re.fullmatch(r"[-*_]{3,}|```.*", s):
            lines.append((re.sub(r"^(#{1,6}\s+|>\s?)", "", s), "plain", 0))
    return lines, tables + ([rows] if rows else [])


WHITE = RGBColor(255, 255, 255)


def _write(paragraph, text: str, size: int | None = None, white: bool = False):
    for part, mark in tools.inline(text):
        run = paragraph.add_run()
        run.text = part
        run.font.bold, run.font.italic = "**" in mark or None, mark in ("*", "***") or None
        if size:
            run.font.size = Pt(size)
        if white:
            run.font.color.rgb = WHITE
        if mark == "`":
            run.font.name = "Courier New"


def _fit(lines: list[tuple[str, str, int]], width: int, height: int) -> int:
    """The largest text size at which the lines fit a box (in EMU), counting the lines that wrap."""
    for size in SIZES:
        per_line = width / 12700 / (size * 0.5)                    # a character is about half as wide as it is tall
        if sum(math.ceil(max(len(text), 1) / per_line) for text, _, _ in lines) * size * 1.25 * 12700 <= height:
            break
    return size


def _widen(prs):
    """The built-in template is 4:3: the slides and everything its layouts place on them become 16:9."""
    prs.slide_width = prs.slide_width * 4 // 3
    for part in (prs.slide_master, *prs.slide_layouts):
        for shape in part.shapes:
            if shape._element.xpath(".//a:xfrm"):                  # the others take their place from the master
                shape.left, shape.width = shape.left * 4 // 3, shape.width * 4 // 3


def _picture(slide, path, left: int, top: int, width: int, height: int):
    """The image as large as the box (in EMU) lets it be, in the middle of it."""
    with Image.open(path) as img:
        scale = min(width / img.width, height / img.height)
        w, h = int(img.width * scale), int(img.height * scale)
    slide.shapes.add_picture(str(path), left + (width - w) // 2, top + (height - h) // 2, w, h)


def to_pptx(deck: list[dict], path):
    """Writes the slides as a PowerPoint file. `path` is a file path or a binary stream. The first slide is a
    title slide when its body is plain text and it has no image; a body's text goes in the content box, its tables
    under it. An image takes the right half of the slide, or all the space under the title when there is no body.
    On a dark background the text is white."""
    prs = Presentation()
    _widen(prs)
    for i, s in enumerate(deck):
        lines, tables = _lines(s["body"])
        image = IMAGES / s["image"] if s.get("image") and (IMAGES / s["image"]).is_file() else None
        cover = i == 0 and not tables and not image and all(kind == "plain" for _, kind, _ in lines)
        slide = prs.slides.add_slide(prs.slide_layouts[0 if cover else 1])
        slide.shapes.title.text = s["title"]
        white = False
        if s.get("background"):
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(s["background"][1:].upper())
            white = dark(s["background"])
            for run in slide.shapes.title.text_frame.paragraphs[0].runs if white else ():
                run.font.color.rgb = WHITE
        box = slide.placeholders[1]
        left, top, width, height = box.left, box.top, box.width, box.height
        if image and (lines or tables):
            width = (width - Pt(18)) // 2
            _picture(slide, image, left + width + Pt(18), top, width, height)
        elif image:
            _picture(slide, image, left, top, width, height)
        rows = sum(map(len, tables))
        text_height = height * len(lines) // (len(lines) + rows + len(tables)) if tables else height
        if not lines:
            box._element.getparent().remove(box._element)
        else:
            if tables or image:
                box.left, box.top, box.width, box.height = left, top, width, text_height
            size = None if cover or (fit := _fit(lines, width, text_height)) == SIZES[0] else fit
            for n, (text, kind, level) in enumerate(lines):
                p = box.text_frame.paragraphs[0] if n == 0 else box.text_frame.add_paragraph()
                p.level = level
                if kind == "number":
                    p._p.get_or_add_pPr().append(p._p.makeelement(qn("a:buAutoNum"), {"type": "arabicPeriod"}))
                elif kind == "plain" and not cover:                # the content box puts a bullet on every paragraph
                    ppr = p._p.get_or_add_pPr()
                    ppr.set("marL", "0")
                    ppr.set("indent", "0")
                    ppr.append(p._p.makeelement(qn("a:buNone"), {}))
                _write(p, text, size, white)
        y = top + (text_height if lines else 0)
        for table in tables:
            cols = max(map(len, table))
            row_height = min(Pt(TABLE_PT * 2.4), (top + height - y) // (rows + len(tables)))
            grid = slide.shapes.add_table(len(table), cols, left, y, width, row_height * len(table)).table
            for r, cells in enumerate(table):
                for c, text in enumerate(cells):
                    _write(grid.cell(r, c).text_frame.paragraphs[0], text, TABLE_PT)
            y += row_height * (len(table) + 1)
        if s["notes"]:
            slide.notes_slide.notes_text_frame.text = s["notes"]
    prs.save(path)
