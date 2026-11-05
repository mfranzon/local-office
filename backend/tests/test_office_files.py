import io

from docx import Document as Docx
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.ingest import _count, _pages


def _docx(tmp_path, build):
    doc = Docx()
    build(doc)
    path = tmp_path / "f.docx"
    doc.save(path)
    return path


def test_docx_splits_at_page_breaks_and_keeps_tables_in_place(tmp_path):
    def build(doc):
        doc.add_paragraph("Contract between Acme and Northwind")
        doc.add_paragraph("Payment terms follow.").add_run().add_break(WD_BREAK.PAGE)
        doc.add_paragraph("Page two intro")
        table = doc.add_table(rows=2, cols=2)
        for r, row in enumerate([("Item", "Total"), ("Licence", "1.200,00")]):
            for c, v in enumerate(row):
                table.cell(r, c).text = v
        doc.add_paragraph("After the table")
        doc.add_paragraph("Annex A").paragraph_format.page_break_before = True
    path = _docx(tmp_path, build)
    pages = [text for _, text in _pages(path)]
    assert len(pages) == _count(path) == 3
    assert "Acme" in pages[0] and "Payment terms" in pages[0]
    assert pages[1].index("Page two intro") < pages[1].index("Licence | 1.200,00") < pages[1].index("After the table")
    assert pages[2] == "Annex A"
    assert "\n\n" in pages[0]          # paragraphs stay separate, so chunking can split long pages


def test_docx_word_rendered_breaks_count_once(tmp_path):
    def build(doc):
        doc.add_paragraph("One").add_run().add_break(WD_BREAK.PAGE)
        p = doc.add_paragraph()
        p.add_run()._r.append(OxmlElement("w:lastRenderedPageBreak"))   # Word writes this right after the explicit one
        p.add_run("Two")
        q = doc.add_paragraph("Three starts")
        mid = q.add_run()._r
        mid.append(OxmlElement("w:lastRenderedPageBreak"))               # a paragraph that flows onto the next page
        t = OxmlElement("w:t")
        t.text = " and ends"
        mid.append(t)
    pages = [text for _, text in _pages(_docx(tmp_path, build))]
    assert pages == ["One", "Two\n\nThree starts", "and ends"]


def test_docx_without_breaks_is_one_page(tmp_path):
    def build(doc):
        for i in range(5):
            doc.add_paragraph(f"Paragraph {i}").paragraph_format.page_break_before = False   # w:val="0", as Google Docs writes
    path = _docx(tmp_path, build)
    assert 'pageBreakBefore w:val="0"' in __import__("zipfile").ZipFile(path).read("word/document.xml").decode()
    assert _count(path) == 1


def test_docx_text_box_is_read_once(tmp_path):
    def build(doc):
        p = doc.add_paragraph("Body")
        mc = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
        alt = etree.SubElement(p._p, mc + "AlternateContent")
        for kind in ("Choice", "Fallback"):
            branch = etree.SubElement(alt, mc + kind)
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = " boxed"
            r.append(t)
            branch.append(r)
    assert [text for _, text in _pages(_docx(tmp_path, build))] == ["Body boxed"]


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_pptx_one_page_per_slide_with_tables_notes_and_picture_slides(tmp_path):
    prs = Presentation()
    blank = prs.slide_layouts[6]
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "Q3 results"
    s1.placeholders[1].text = "Revenue up 12%"
    s1.notes_slide.notes_text_frame.text = "Mention the Northwind deal"
    prs.slides.add_slide(blank)                                     # empty: still a page
    s3 = prs.slides.add_slide(blank)
    rows = s3.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    for r, row in enumerate([("Region", "Sales"), ("North", "4.100")]):
        for c, v in enumerate(row):
            rows.cell(r, c).text = v
    s4 = prs.slides.add_slide(blank)
    s4.shapes.add_picture(_png("white"), Inches(0), Inches(0), Inches(1), Inches(1))
    s4.shapes.add_picture(_png("red"), Inches(1), Inches(1), Inches(6), Inches(4))
    path = tmp_path / "deck.pptx"
    prs.save(path)

    pages = list(_pages(path))
    assert len(pages) == _count(path) == 4
    assert pages[0] == (None, "Q3 results\n\nRevenue up 12%\n\nNotes: Mention the Northwind deal")
    assert pages[1] == (None, "")
    assert pages[2] == (None, "Region | Sales\nNorth | 4.100")
    img, text = pages[3]
    assert text is None and img.getpixel((10, 10)) == (255, 0, 0)    # the largest picture goes to OCR
