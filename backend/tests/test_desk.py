import asyncio
import io
import json

import pytest
from docx import Document as Docx
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pptx.util import Inches

from app import db as dbmod
from app import desk, llm, main, sheet, tools


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tools, "EXPORTS", tmp_path / "exports")
    dbmod.init()
    return TestClient(main.app, base_url="http://127.0.0.1")


def test_formulas_compute_and_errors_spread():
    rows = sheet.clean([["Item", "Amount", "Qty"], ["Paper", 120, 2], ["Ink", 80.5, 1],
                        ["Total", "=somma(b2:B3)", "=B2*C2+B3"],                  # Italian name, lower-case cells
                        ["=IF(C9=0;0;B2/C9)", "=B2/C9", "=B5+1"],                 # ";" as typed in Italian Excel
                        ["=A6", "=NOPE(1)", '=ROUND(B4/3, 1)&" EUR; net"'], [None, "  "], []])
    assert rows[3] == ["Total", "=SUM(B2:B3)", "=B2*C2+B3"] and rows[4][0] == "=IF(C9=0,0,B2/C9)" and len(rows) == 6
    shown = sheet.values(rows)
    assert shown[3] == ["Total", 200.5, 320.5]
    assert shown[4] == [0, "#DIV/0!", "#DIV/0!"]                                  # IF is lazy; an error spreads to its readers
    assert shown[5] == ["#CYCLE!", "#NAME?", "66.8 EUR; net"]                     # text in quotes is left alone
    assert [sheet.values([[f]])[0][0] for f in ("=-2^2", "=50%", "=ROUND(2.5)", "=1+", '="a"="A"', "=2>1")] == [4, 0.5, 3, "#ERROR!", True, True]


def test_inserting_and_deleting_rows_moves_references():
    rows = sheet.clean([["Item", "Amount"], ["Paper", 120], ["Ink", 80], ["Pens", 5], ["Total", "=SUM(B2:B4)+$B$2"]])
    assert sheet.shift(rows, "row", 2, 2)[-1] == ["Total", "=SUM(B2:B6)+$B$2"]
    assert sheet.shift(rows, "row", 3, -1)[-1] == ["Total", "=SUM(B2:B3)+$B$2"]    # the range shrinks
    assert sheet.values(sheet.shift(rows, "row", 1, -1))[-1] == ["Total", "#REF!"]  # a single deleted cell can't be followed
    assert sheet.shift(rows, "col", 0, 1)[-1] == [None, "Total", "=SUM(C2:C4)+$C$2"]
    assert sheet.shift(rows, "col", 0, -1)[-1] == ["=SUM(A2:A4)+$A$2"]
    with pytest.raises(ValueError, match="at most"):
        sheet.clean([[1]] * (sheet.MAX_ROWS + 1))


def test_blocks_are_what_blank_lines_separate():
    md = "# Title\n\nFirst\nparagraph\n\n\n- one\n- two\n\n```\ncode\n\nmore\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert [b.splitlines()[0] for b in desk.blocks(md)] == ["# Title", "First", "- one", "```", "| A | B |"]
    assert "\n\n".join(desk.blocks(md)) == md.replace("\n\n\n", "\n\n")


def test_word_file_round_trip(client):
    doc = Docx()
    doc.add_heading("Offer 2027", level=1)
    p = doc.add_paragraph("Total ")
    p.add_run("4,000 EUR").bold = True
    p.add_run(" due *soon*").italic = True
    doc.add_paragraph("First", style="List Bullet")
    doc.add_paragraph("Nested", style="List Bullet 2")
    doc.add_paragraph("Step", style="List Number")
    table = doc.add_table(rows=2, cols=2)
    for r, row in enumerate([("Item", "Total"), ("Licence | Pro", "1.200,00")]):
        for c, v in enumerate(row):
            table.cell(r, c).text = v
    buf = io.BytesIO()
    doc.save(buf)
    file = client.post("/api/desk/import", files={"file": ("Offer 2027.docx", buf.getvalue())}).json()
    assert file["kind"] == "doc" and file["name"] == "Offer 2027"
    assert desk.blocks(file["content"]) == [
        "# Offer 2027", "Total **4,000 EUR** *due \\*soon\\**", "- First\n  - Nested", "1. Step",
        "| Item | Total |\n| --- | --- |\n| Licence \\| Pro | 1.200,00 |"]

    back = Docx(io.BytesIO(client.get(f"/api/desk/{file['id']}/export?format=docx").content))
    assert [(p.style.name, p.text) for p in back.paragraphs] == [
        ("Heading 1", "Offer 2027"), ("Normal", "Total 4,000 EUR due *soon*"), ("List Bullet", "First"),
        ("List Bullet 2", "Nested"), ("List Number", "Step")]
    runs = back.paragraphs[1].runs
    assert [(r.text, r.bold, r.italic) for r in runs] == [("Total ", None, None), ("4,000 EUR", True, None), (" ", None, None), ("due *soon*", None, True)]
    assert back.tables[0].cell(1, 0).text == "Licence | Pro"
    assert client.get(f"/api/desk/{file['id']}/export?format=xlsx").status_code == 400


def test_excel_file_round_trip_keeps_formulas(client):
    wb = Workbook()
    for row in (["Item", "Amount"], ["Paper", 120], ["Ink", 80.5], ["Total", "=SUM(B2:B3)"]):
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    file = client.post("/api/desk/import", files={"file": ("Costs.xlsx", buf.getvalue())}).json()
    assert file["content"][3] == ["Total", "=SUM(B2:B3)"] and file["values"][3] == ["Total", 200.5]

    saved = client.put(f"/api/desk/{file['id']}", json={"content": file["content"] + [["Average", "=media(B2:B3)"]]}).json()
    assert saved["content"][4][1] == "=AVERAGE(B2:B3)" and saved["values"][4][1] == 100.25
    moved = client.post(f"/api/desk/{file['id']}/shift", json={"axis": "row", "index": 1, "delta": 1}).json()
    assert moved["content"][1] == [] and moved["content"][4] == ["Total", "=SUM(B3:B4)"]

    ws = load_workbook(io.BytesIO(client.get(f"/api/desk/{file['id']}/export?format=xlsx").content)).active
    assert ws["B5"].value == "=SUM(B3:B4)" and ws["B3"].value == 120
    assert client.get(f"/api/desk/{file['id']}/export?format=csv").content.decode("utf-8-sig").splitlines()[4] == "Total,200.5"

    csv = client.post("/api/desk/import", files={"file": ("list.csv", "Codice;Importo\n00123;45\n".encode())}).json()
    assert csv["content"] == [["Codice", "Importo"], ["00123", 45]]
    assert client.post("/api/desk/import", files={"file": ("scan.pdf", b"%PDF")}).status_code == 400


def test_powerpoint_file_round_trip(client):
    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text, cover.placeholders[1].text = "Offer 2027", "Northwind Ltd"
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "What is\vincluded"
    frame = slide.placeholders[1].text_frame
    frame.text = "Licence"
    frame.paragraphs[0].add_run().text = " Pro"
    frame.paragraphs[0].runs[1].font.bold = True
    frame.add_paragraph().text = "12 months"
    frame.paragraphs[1].level = 1
    box = slide.shapes.add_textbox(Inches(1), Inches(6), Inches(4), Inches(1))
    box.text_frame.text = "Prices without VAT"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(4.5), Inches(4), Inches(1)).table
    for r, row in enumerate([("Item", "Total"), ("Licence | Pro", "1.200,00")]):
        for c, v in enumerate(row):
            table.cell(r, c).text = v
    slide.notes_slide.notes_text_frame.text = "Mention the discount."
    buf = io.BytesIO()
    prs.save(buf)
    file = client.post("/api/desk/import", files={"file": ("Offer.pptx", buf.getvalue())}).json()
    assert file["kind"] == "slides" and file["content"] == [
        {"title": "Offer 2027", "body": "Northwind Ltd", "notes": ""},
        {"title": "What is included", "notes": "Mention the discount.",
         "body": "- Licence **Pro**\n  - 12 months\n\n| Item | Total |\n| --- | --- |\n| Licence \\| Pro | 1.200,00 |\n\nPrices without VAT"}]

    deck = file["content"] + [{"title": "Next steps", "body": "1. Sign\n2. *Pay*\n\nThanks", "notes": 5}, {"title": "  ", "junk": 1}]
    saved = client.put(f"/api/desk/{file['id']}", json={"content": deck}).json()
    assert saved["content"][2:] == [{"title": "Next steps", "body": "1. Sign\n2. *Pay*\n\nThanks", "notes": "5"}, {"title": "", "body": "", "notes": ""}]
    back = Presentation(io.BytesIO(client.get(f"/api/desk/{file['id']}/export?format=pptx").content))
    assert back.slide_width * 9 == back.slide_height * 16
    assert [s.slide_layout.name for s in back.slides] == ["Title Slide", "Title and Content", "Title and Content", "Title and Content"]
    table_md = "\n\n| Item | Total |\n| --- | --- |\n| Licence \\| Pro | 1.200,00 |"
    assert desk.pptx_to_slides(back) == [{**s, "body": (s["body"].replace(table_md, "") + (table_md if table_md in s["body"] else "")).replace("2. *Pay*", "1. *Pay*")}
                                         for s in saved["content"]]              # text comes before tables; a numbered list counts by itself
    second, third = back.slides[1], back.slides[2]
    assert [(r.text, r.font.bold) for r in second.placeholders[1].text_frame.paragraphs[0].runs] == [("Licence ", None), ("Pro", True)]
    assert second.shapes[-1].table.cell(1, 0).text == "Licence | Pro" and second.shapes[-1].top >= second.placeholders[1].top + second.placeholders[1].height
    assert third.placeholders[1].text_frame.paragraphs[1].runs[0].font.italic and len(back.slides[3].placeholders) == 1
    assert client.get(f"/api/desk/{file['id']}/export?format=docx").status_code == 400


def _png(color="red", size=(40, 20)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_slides_keep_a_background_colour_and_an_image(client, monkeypatch, tmp_path):
    from pptx.dml.color import RGBColor
    from app import ingest, slides
    monkeypatch.setattr(slides, "IMAGES", tmp_path / "desk_images")
    monkeypatch.setattr(ingest, "FILES", tmp_path)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text, slide.placeholders[1].text = "Team", "Anna"
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    slide.shapes.add_picture(io.BytesIO(_png()), Inches(5), Inches(2), Inches(2))
    buf = io.BytesIO()
    prs.save(buf)
    file = client.post("/api/desk/import", files={"file": ("Team.pptx", buf.getvalue())}).json()
    image = file["content"][0]["image"]
    assert file["content"] == [{"title": "Team", "body": "- Anna", "notes": "", "background": "#1f3a5f", "image": image}]
    assert client.get(f"/api/desk/images/{image}").headers["content-type"] == "image/jpeg"
    assert client.get("/api/desk/images/nope.jpg").status_code == 404
    assert client.post("/api/desk/images", files={"file": ("x.png", b"not a picture")}).status_code == 400
    mine = client.post("/api/desk/images", files={"file": ("x.png", _png("blue"))}).json()["image"]
    saved = client.put(f"/api/desk/{file['id']}", json={"content": file["content"] + [
        {"title": "Cover", "background": "FFF", "image": mine}, {"title": "Plain", "background": "blue", "image": "../x.jpg"}]}).json()
    assert saved["content"][1:] == [{"title": "Cover", "body": "", "notes": "", "background": "#ffffff", "image": mine},
                                    {"title": "Plain", "body": "", "notes": ""}]

    with dbmod.db() as conn:
        conn.executemany("INSERT INTO documents(id, name, ext, size) VALUES (?,?,?,0)", [("pic1", "logo.png", ".png"), ("txt1", "notes.txt", ".txt")])
    (tmp_path / "pic1.png").write_bytes(_png("green"))
    prompts = _assistant(monkeypatch, [("set_background", {"color": "#0B1F3A"}), ("set_background", {"color": "navy"}),
                                       ("write_slide", {"slide": 3, "image": "pic1", "background": ""}), ("write_slide", {"slide": 2, "image": "txt1"}),
                                       ("add_slides", {"after": 3, "slides": [{"title": "End", "image": image, "background": "#fff"}]})], "Done.")
    _chat(file["id"], "make it dark blue and put the logo on the last slide")
    deck = desk.get(file["id"])["content"]
    assert [s.get("background") for s in deck] == ["#0b1f3a", "#0b1f3a", None, "#ffffff"]
    assert deck[2]["image"] not in (image, mine) and deck[1]["image"] == mine and deck[3]["image"] == image
    assert f"[1] Title: Team\n- Anna\nBackground: #0b1f3a\nImage: {image}" in prompts[1]

    back = Presentation(io.BytesIO(client.get(f"/api/desk/{file['id']}/export?format=pptx").content))
    assert desk.pptx_to_slides(back) == deck
    first, second = back.slides[0], back.slides[1]
    assert first.shapes.title.text_frame.paragraphs[0].runs[0].font.color.rgb == RGBColor(255, 255, 255)   # white on a dark colour
    picture, wide = first.shapes[-1], second.shapes[-1]
    assert picture.left > first.placeholders[1].left + first.placeholders[1].width and picture.width == 2 * picture.height
    assert wide.width > 1.5 * picture.width and len(second.placeholders) == 1        # no text: the image has all the space

    import os
    for name in os.listdir(slides.IMAGES):
        os.utime(slides.IMAGES / name, (0, 0))
    other = desk.create("Other", "slides", [{"title": "x", "image": mine}])
    desk.delete(file["id"])
    assert os.listdir(slides.IMAGES) == [mine]                                       # the images no slide shows are removed
    desk.delete(other)


def test_long_slide_text_is_made_smaller_to_fit():
    from app import slides
    buf = io.BytesIO()
    slides.to_pptx(slides.clean([{"title": "Short", "body": "- one\n- two"}, {"title": "Long", "body": "\n".join(f"- point {i} " + "word " * 12 for i in range(12))}]), buf)
    short, long = (s.placeholders[1].text_frame.paragraphs[0].runs[0].font.size for s in Presentation(buf).slides)
    assert short is None and long.pt == 20                               # the template's size, unless the text needs a smaller one
    with pytest.raises(ValueError, match="at most"):
        slides.clean([{}] * (slides.MAX_SLIDES + 1))


def test_files_are_created_renamed_listed_and_deleted(client, tmp_path):
    a = client.post("/api/desk", json={"name": "Notes", "kind": "doc"}).json()
    b = client.post("/api/desk", json={"name": "Budget", "kind": "sheet"}).json()
    assert a["content"] == "" and b["content"] == [] and not a["can_undo"]
    assert client.put(f"/api/desk/{a['id']}", json={"name": "Meeting notes", "content": "Hello"}).json()["name"] == "Meeting notes"
    assert {f["name"] for f in client.get("/api/desk").json()} == {"Meeting notes", "Budget"}
    client.delete(f"/api/desk/{a['id']}")
    assert client.get(f"/api/desk/{a['id']}").status_code == 404 and len(client.get("/api/desk").json()) == 1

    (tmp_path / "exports/run1").mkdir(parents=True)                       # a file a skill created opens on the Desk
    tools.markdown_to_docx("# Report\n\nBody", tmp_path / "exports/run1/Report.docx")
    assert client.post("/api/desk/copy", json={"export": "run1/Report.docx"}).json()["content"] == "# Report\n\nBody"
    assert client.post("/api/desk/copy", json={"export": "../office.db"}).status_code == 404


def _assistant(monkeypatch, *replies):
    """The model's replies, in order: lists of (tool, arguments) calls, then the answer."""
    queue = iter(replies)
    prompts = []

    async def fake_complete(role, messages, **params):
        prompts.append(messages[0]["content"])
        reply = next(queue)
        if isinstance(reply, str):
            return {"content": reply}
        return {"content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(reply)]}
    async def fake_stream(role, messages, **params):      # the reply arrives in pieces, as from the model
        whole = await fake_complete(role, messages, **params)
        calls = whole.get("tool_calls") or []
        for cut in (15, 40, 60):
            yield {"content": "", "tool_calls": [{**c, "function": {**c["function"], "arguments": c["function"]["arguments"][:cut]}} for c in calls]}
        yield whole
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "complete_stream", fake_stream)
    return prompts


def _chat(file, text, selection=None):
    async def collect():
        return [e async for e in desk.assist(desk.get(file), [{"role": "user", "content": text}], selection)]
    return asyncio.run(collect())


def test_assistant_edits_a_document_and_the_change_can_be_undone(client, monkeypatch):
    file = desk.create("Offer", "doc", "# Offer\n\nWe offer the licence.\n\nRegards")
    prompts = _assistant(monkeypatch,
                         [("replace_blocks", {"start": 2, "end": 2, "content": "[2] We are pleased to offer the licence."})],
                         [("insert_blocks", {"after": 2, "content": "- Support included\n- 12 months"}), ("replace_blocks", {"start": 9, "end": 9, "content": "x"})],
                         "I made the second paragraph more formal and added a list.")
    events = _chat(file, "make this more formal and add what's included", {"start": 2, "end": 2, "text": "We offer the licence."})
    drafts = [e for e in events if e["type"] == "draft"]                 # the words arrive before the edit is made
    assert events[0]["type"] == "draft" and {(d["key"], d["start"], d["end"]) for d in drafts} == {("0.0", 2, 2), ("1.0", 3, 2), ("1.1", 9, 9)}
    assert "".join(d["text"] for d in drafts if d["key"] == "0.0") == "[2] We are pleased to offer the licence."
    assert "".join(d["text"] for d in drafts if d["key"] == "1.0") == "- Support included\n- 12 months"
    events = [e for e in events if e["type"] != "draft"]
    assert [e["type"] for e in events] == ["step", "edit", "step", "edit", "step", "edit", "sources", "token"]
    assert "[2] We offer the licence." in prompts[0] and "selected this text in block 2" in prompts[0]
    assert "[2] We are pleased to offer the licence." in prompts[1] and "selected" not in prompts[1]   # the prompt follows the file
    assert "[3] - Support included\n- 12 months\n\n[4] Regards" in prompts[2]
    after = desk.get(file)
    assert after["content"] == "# Offer\n\nWe are pleased to offer the licence.\n\n- Support included\n- 12 months\n\nRegards" and after["can_undo"]
    assert client.post(f"/api/desk/{file}/undo").json() == {**after, "content": "# Offer\n\nWe offer the licence.\n\nRegards", "can_undo": False,
                                                           "updated_at": desk.get(file)["updated_at"]}

    _assistant(monkeypatch, [("insert_blocks", {"after": 0, "content": "DRAFT"})], "Marked as a draft.")
    _chat(file, "mark it as a draft")
    assert desk.get(file)["can_undo"]
    saved = client.put(f"/api/desk/{file}", json={"content": "DRAFT\n\n# Offer\n\nMy own words"}).json()
    assert not saved["can_undo"]                                         # undoing now would also lose what the user wrote since


def test_assistant_fills_a_sheet(client, monkeypatch):
    file = desk.create("Costs", "sheet", [["Item", "Amount"], ["Paper", 120], ["Ink", 80]])
    prompts = _assistant(monkeypatch, [("write_cells", {"start": "A4", "rows": [["Total", "=SUM(B2:B3)"]]}),
                                       ("insert_rows", {"row": 2}), ("write_cells", {"start": "Z0", "rows": [[1]]}),
                                       ("create_file", {"filename": "x", "format": "md", "content": "x"})], "Added a total.")
    events = _chat(file, "add a total", {"cell": "B3"})
    assert [e["type"] for e in events] == ["step", "edit", "step", "edit", "step", "step", "sources", "token"]
    assert "selected cell is B3" in prompts[0] and "A2: Paper | B2: 120" in prompts[0]
    assert "A5: Total | B5: =SUM(B3:B4) (shows 200)" in prompts[1]
    assert desk.get(file)["values"] == [["Item", "Amount"], [], ["Paper", 120], ["Ink", 80], ["Total", 200]]
    assert not (tools.EXPORTS).exists()                                  # only the open file can be changed


def test_assistant_writes_a_presentation(client, monkeypatch):
    file = desk.create("Pitch", "slides", [{"title": "Pitch", "body": "Northwind", "notes": ""}, {"title": "Old", "body": "- a"}])
    prompts = _assistant(monkeypatch,
                         [("add_slides", {"after": 1, "slides": [{"title": "Problem", "body": "- Slow\n- Costly"}, {"title": "Solution", "notes": "Demo here"}]}),
                          ("write_slide", {"slide": 4, "body": "- b", "notes": "Say b"})],
                         [("move_slide", {"slide": 4, "to": 2}), ("delete_slides", {"slide": 9}), ("write_slide", {"slide": 0, "title": "x"})],
                         [("delete_slides", {"slide": 4})], "Added two slides.")
    events = _chat(file, "add the problem and the solution", {"slide": 2})
    assert [(e["type"], e.get("slide")) for e in events] == [("step", None), ("edit", 2), ("step", None), ("edit", 4), ("step", None), ("edit", 2),
                                                           ("step", None), ("step", None), ("step", None), ("edit", 3), ("sources", None), ("token", None)]
    assert "a presentation that is open" in prompts[0] and "[2] Title: Old\n- a" in prompts[0] and "looking at slide 2" in prompts[0]
    assert "[4] Title: Old\n- b\nNotes: Say b" in prompts[1] and "looking at" not in prompts[1]
    after = desk.get(file)
    assert [s["title"] for s in after["content"]] == ["Pitch", "Old", "Problem"] and after["can_undo"]
    assert [s["title"] for s in client.post(f"/api/desk/{file}/undo").json()["content"]] == ["Pitch", "Old"]

    long = desk.create("Long", "slides", [{"title": f"Slide {i}", "body": "word " * 100} for i in range(1, 21)])
    prompts = _assistant(monkeypatch, [("read_slides", {"first": 20, "last": 20})], "ok")
    _chat(long, "what does this slide say?", {"slide": 3})
    assert len(prompts[0]) < 9000 and "cut: read_slides" in prompts[0] and "[3] Title: Slide 3\n" + "word " * 99 + "word\n" in prompts[0]
    empty = desk.create("New", "slides")
    prompts = _assistant(monkeypatch, "Nothing to do.")
    _chat(empty, "hi", {"slide": 1})
    assert "has no slides yet: add them with add_slides" in prompts[0] and "looking at" not in prompts[0]


def test_unfinished_text_of_an_edit_is_read():
    assert desk._unfinished_string('Total \\"4,000\\"\\nNext') == 'Total "4,000"\nNext'
    assert desk._unfinished_string('caf\\u00e9 \\u00') == "café " and desk._unfinished_string("line\\") == "line"
    assert desk._unfinished_string('done", "x": 1}') == "done"
    late = {"tool_calls": [{"function": {"name": "replace_blocks", "arguments": '{"content": "text", "start": 1'}}]}
    assert desk.Draft().events(0, late) == []                           # no position yet: nothing to show


def test_long_document_is_shown_as_an_outline(client, monkeypatch):
    file = desk.create("Long", "doc", "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(1, 21)))
    prompts = _assistant(monkeypatch, [("read_blocks", {"start": 20, "end": 20})], "ok")
    _chat(file, "what does the last paragraph say?", {"start": 3, "end": 3, "text": ""})
    assert len(prompts[0]) < 9000 and "[20] Paragraph 20 word" in prompts[0] and "cut: read_blocks" in prompts[0]
    assert "[3] Paragraph 3 " + "word " * 119 + "word\n" in prompts[0]    # the block the cursor is in stays whole
