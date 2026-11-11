"""The Desk: documents and spreadsheets the user edits in the app, together with the assistant.

A Desk file is the app's own copy: a document is Markdown, a spreadsheet is rows of cells (sheet.py). Word, Excel,
CSV and text files are imported into that form and exported back, so the originals are never modified.
The assistant edits the open file with tools (replace or insert blocks of a document, write cells of a sheet);
the content before its first change in a run is kept, so the user can undo the whole run.
"""
import csv
import io
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from itertools import groupby
from pathlib import Path

from docx import Document as Docx
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from openpyxl import Workbook, load_workbook

from . import agent, mcp, sheet, tools
from .db import db

IMPORTS = {".docx": "doc", ".md": "doc", ".txt": "doc", ".xlsx": "sheet", ".csv": "sheet"}
EXPORTS = {"doc": ("docx", "md"), "sheet": ("xlsx", "csv")}
DOC_CHARS = 6000      # a longer document is shown to the assistant as an outline; it reads blocks as it needs them
SHEET_CHARS = 5000
READ_CHARS = 6000


# --- storage ---

def view(row) -> dict:
    out = {k: row[k] for k in ("id", "name", "kind", "updated_at")} | {"can_undo": row["previous"] is not None}
    if row["kind"] == "sheet":
        rows = json.loads(row["content"])
        return out | {"content": rows, "values": sheet.values(rows)}
    return out | {"content": row["content"]}


def listing() -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, name, kind, updated_at FROM desk_files ORDER BY updated_at DESC, rowid DESC")]


def get(file_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM desk_files WHERE id=?", (file_id,)).fetchone()
    return view(row) if row else None


def _stored(kind: str, content) -> str:
    if kind == "sheet":
        return json.dumps(sheet.clean(content if isinstance(content, list) else []))
    return content.strip() if isinstance(content, str) else ""


def create(name: str, kind: str, content=None) -> str:
    file_id = uuid.uuid4().hex[:12]
    with db() as conn:
        conn.execute("INSERT INTO desk_files(id, name, kind, content) VALUES (?,?,?,?)",
                     (file_id, name.strip()[:120] or "Untitled", kind, _stored(kind, content)))
    return file_id


def save(file_id: str, name: str | None = None, content=None):
    """The user's own change. It ends the offer to undo the assistant's changes: taking those back now would also
    throw away what the user did after them."""
    with db() as conn:
        row = conn.execute("SELECT kind FROM desk_files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(file_id)
        if name is not None and name.strip():
            conn.execute("UPDATE desk_files SET name=? WHERE id=?", (name.strip()[:120], file_id))
        if content is not None:
            conn.execute("UPDATE desk_files SET content=?, previous=NULL, updated_at=datetime('now') WHERE id=?",
                         (_stored(row["kind"], content), file_id))


def undo(file_id: str):
    with db() as conn:
        conn.execute("UPDATE desk_files SET content=previous, previous=NULL, updated_at=datetime('now') "
                     "WHERE id=? AND previous IS NOT NULL", (file_id,))


def delete(file_id: str):
    with db() as conn:
        conn.execute("DELETE FROM desk_files WHERE id=?", (file_id,))


# --- documents: Markdown in numbered blocks ---

def blocks(md: str) -> list[str]:
    """A paragraph, a heading, a whole list or a whole table: whatever blank lines separate (not inside code)."""
    out, cur, fence = [], [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if line.strip() or fence:
            cur.append(line.rstrip())
        elif cur:
            out.append("\n".join(cur))
            cur = []
    return out + (["\n".join(cur)] if cur else [])


def escape(text: str) -> str:
    """Plain text as Markdown that shows the same characters."""
    text = re.sub(r"([\\*`\[<|])", r"\\\1", text)
    text = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", text)
    return re.sub(r"^(\d+)([.)]\s)", r"\1\\\2", re.sub(r"^(#{1,6}\s|>|[-+]\s)", r"\\\1", text))


def _marked(text: str, mark: str) -> str:
    inner = text.strip()
    return text if not inner or not mark else text[:len(text) - len(text.lstrip())] + mark + inner + mark + text[len(text.rstrip()):]


def _inline(paragraph) -> str:
    """A Word paragraph's text with bold, italic and links, as Markdown."""
    pieces = []   # (text, mark)
    for item in paragraph.iter_inner_content():
        if isinstance(item, Hyperlink):
            pieces.append((f"[{escape(item.text)}]({item.url})" if item.url else escape(item.text), ""))
        elif item.text:
            pieces.append((escape(item.text), "*" * ((2 if item.bold else 0) + (1 if item.italic else 0))))
    text = "".join(_marked("".join(t for t, _ in group), mark) for mark, group in groupby(pieces, key=lambda p: p[1]))
    return re.sub(r"\s*\n\s*", "\\\n", text.replace("\t", " ").strip())


def _list_marker(doc, paragraph) -> str | None:
    """'- ' or '1. ' (indented for nested levels) when the paragraph is a list item."""
    style = paragraph.style.name if paragraph.style is not None else ""
    ppr = paragraph._p.pPr
    num = ppr.numPr if ppr is not None else None
    if num is None and not style.startswith("List "):
        return None
    level, numbered = 0, "Number" in style
    if m := re.search(r"(\d)$", style):
        level = int(m[1]) - 1
    if num is not None and num.numId is not None:
        level = num.ilvl.val if num.ilvl is not None else 0
        try:
            root = doc.part.numbering_part.element
            abstract = root.xpath(f"./w:num[@w:numId='{num.numId.val}']/w:abstractNumId/@w:val")[0]
            numbered = root.xpath(f"./w:abstractNum[@w:abstractNumId='{abstract}']/w:lvl[@w:ilvl='{level}']/w:numFmt/@w:val")[0] != "bullet"
        except (IndexError, KeyError, NotImplementedError):
            pass
    marker = "1. " if numbered else "- "
    return " " * (len(marker) * level) + marker


def table_markdown(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    lines = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
    return "\n".join([lines[0], "|" + " --- |" * width, *lines[1:]])


def docx_to_markdown(doc) -> str:
    out: list[str] = []
    in_list = None            # the marker of the list being written: a bullet list ends where a numbered one starts
    for item in doc.iter_inner_content():
        if isinstance(item, Table):
            rows = [[" ".join(filter(None, (_inline(p) for p in cell.paragraphs))).replace("\\\n", " ") for cell in row.cells]
                    for row in item.rows]
            if rows:
                out.append(table_markdown(rows))
            in_list = None
            continue
        text = _inline(item)
        if not text:
            continue
        style = item.style.name if item.style is not None else ""
        marker = _list_marker(doc, item)
        if marker and in_list and (marker[0] == " " or marker == in_list):
            out[-1] += "\n" + marker + text
        elif marker:
            out.append(marker.lstrip() + text)
            in_list = marker.lstrip()
        elif m := re.match(r"Heading (\d)", style):
            out.append("#" * min(int(m[1]), 6) + " " + re.sub(r"^\*\*(.*)\*\*$", r"\1", text))
        else:
            out.append(("# " if style == "Title" else "> " if "Quote" in style else "") + text)
        in_list = in_list if marker else None
    return "\n\n".join(out)


# --- import and export ---

def _xlsx_value(v):
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat(" ", "minutes")
    if isinstance(v, (date, time)):
        return v.isoformat()
    return v if v is None or isinstance(v, (bool, int, float, str)) else str(getattr(v, "text", v))


def read_file(filename: str, data: bytes) -> tuple[str, object]:
    """(kind, content) of a Word, Excel, CSV, Markdown or text file."""
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        return "doc", docx_to_markdown(Docx(io.BytesIO(data)))
    if ext == ".xlsx":
        ws = load_workbook(io.BytesIO(data)).active
        return "sheet", [[_xlsx_value(v) for v in row] for row in ws.iter_rows(
            max_row=min(ws.max_row, sheet.MAX_ROWS + 1), max_col=min(ws.max_column, sheet.MAX_COLS + 1), values_only=True)]
    if ext not in IMPORTS:
        raise ValueError("the Desk opens Word (.docx), Excel (.xlsx), CSV, Markdown and text files")
    text = data.decode("utf-8-sig", errors="replace")
    if ext == ".csv":
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return "sheet", [[sheet.number(v) for v in row] for row in csv.reader(io.StringIO(text), dialect)]
    return "doc", text if ext == ".md" else "\n\n".join(escape(p.strip()).replace("\n", "\\\n") for p in re.split(r"\n\s*\n", text) if p.strip())


def import_file(filename: str, data: bytes) -> str:
    kind, content = read_file(filename, data)
    return create(Path(filename).stem, kind, content)


def export(file: dict, fmt: str) -> tuple[str, bytes]:
    """(filename, bytes) of a Desk file as docx or md (documents), xlsx or csv (sheets)."""
    if fmt not in EXPORTS[file["kind"]]:
        raise ValueError(f"a {file['kind']} exports as {' or '.join(EXPORTS[file['kind']])}")
    buf = io.BytesIO()
    if fmt == "docx":
        tools.markdown_to_docx(file["content"], buf)
    elif fmt == "xlsx":
        wb = Workbook()
        for row in file["content"]:
            wb.active.append(row)
        wb.save(buf)
    elif fmt == "csv":
        text = io.StringIO()
        csv.writer(text).writerows(file["values"])
        buf.write(text.getvalue().encode("utf-8-sig"))   # the BOM makes Excel read accents correctly
    else:
        buf.write(file["content"].encode())
    return tools._safe_name(file["name"], fmt), buf.getvalue()


# --- the assistant edits the open file ---

SYSTEM = """You are an office assistant working with the user on "{name}", a {what} that is open in front of them. Today is {today}.
The user sees your changes appear in the file as you make them, and can undo them.
- When the user asks for a change or for new content, make it in the file with the tools, then say in one or two
  sentences what you did. Never paste the new content into the chat instead of putting it in the file.
- When the user only asks a question about the file, answer it and change nothing.
- When you rewrite text (more formal, shorter, corrected, translated), keep what it says: add no terms, amounts,
  dates or conditions that are not already there.
- Use the document and wiki tools when the request needs facts from the office's documents. Never invent facts,
  names or numbers. In your reply, not in the file, cite those facts with the source numbers that tool results
  give, e.g. [2].
- Write in the file in the language it is already written in, unless the user asks for another. Reply in the
  language of the user's request: "{request}"{services}

{how}

## The {what} as it is now
{content}{selection}"""

DOC_HOW = """The document is Markdown, shown below as numbered blocks: a paragraph, a heading, a whole list or a whole table is
one block. The [n] numbers are not part of the text: never write them.
- replace_blocks rewrites blocks start..end with new Markdown; with empty content it deletes them.
- insert_blocks adds new Markdown after a block (after 0: at the top).
Change only the blocks the request is about and leave the rest as it is. The numbers change after every edit:
the list below is always the current one."""

SHEET_HOW = """The cells that have content are listed below as address: content. A formula starts with "=" and its result is
shown after it.
- write_cells writes a block of cells, row by row, from a top-left cell. Numbers go in as numbers. "" empties a
  cell, null leaves a cell as it is.
- Use formulas for totals and everything computed (=SUM(B2:B9), =B2*C2, also AVERAGE, MIN, MAX, COUNT, ROUND, IF),
  so the sheet stays right when the user changes a number.
- insert_rows and delete_rows move the rows below; formulas follow."""

_INT = {"type": "integer"}
DOC_TOOLS = [
    ("replace_blocks", "Replace blocks start..end of the open document with new Markdown. Empty content deletes them.",
     {"start": _INT, "end": _INT, "content": {"type": "string", "description": "Markdown"}}, ["start", "end", "content"]),
    ("insert_blocks", "Add new Markdown to the open document after a block (after 0: at the top).",
     {"after": _INT, "content": {"type": "string", "description": "Markdown"}}, ["after", "content"]),
]
READ_BLOCKS = ("read_blocks", "The full text of blocks start..end of the open document.", {"start": _INT, "end": _INT}, ["start", "end"])
SHEET_TOOLS = [
    ("write_cells", "Write a block of cells of the open sheet, row by row, starting at a top-left cell.",
     {"start": {"type": "string", "description": "top-left cell, e.g. A1"},
      "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}},
               "description": 'each row is a list of cell contents: a number, text, a formula like "=SUM(B2:B9)", "" to empty, null to keep'}},
     ["start", "rows"]),
    ("insert_rows", "Insert empty rows above a row of the open sheet.",
     {"row": {"type": "integer", "description": "row number, from 1"}, "count": {"type": "integer", "description": "default 1"}}, ["row"]),
    ("delete_rows", "Delete rows of the open sheet.",
     {"row": {"type": "integer", "description": "first row to delete, from 1"}, "count": {"type": "integer", "description": "default 1"}}, ["row"]),
]
READ_CELLS = ("read_cells", "The cells of rows first..last of the open sheet.", {"first": _INT, "last": _INT}, ["first", "last"])
READ_TOOLS = {"search_documents", "list_documents", "read_document", "wiki_page", "list_deadlines"}


class Session:
    """One assistant run on a Desk file."""

    def __init__(self, file: dict):
        self.id, self.kind = file["id"], file["kind"]
        self.edits = 0

    def content(self):
        return get(self.id)["content"]

    def write(self, content):
        """The content before the first change of the run is kept, so undo() takes the whole run back."""
        stored = _stored(self.kind, content)
        with db() as conn:
            if not self.edits:
                conn.execute("UPDATE desk_files SET previous=content WHERE id=?", (self.id,))
            conn.execute("UPDATE desk_files SET content=?, updated_at=datetime('now') WHERE id=?", (stored, self.id))
        self.edits += 1


def numbered(bs: list[str], whole: range = range(0)) -> str:
    """Blocks as the assistant sees them. A long document is an outline: each block cut short, except `whole`."""
    long = sum(map(len, bs)) > DOC_CHARS
    return "\n\n".join(f"[{i}] {b}" if not long or i in whole or len(b) <= 200 else f"[{i}] {b[:160]} … (cut: read_blocks shows the rest)"
                       for i, b in enumerate(bs, 1)) or "(the document is empty: add content with insert_blocks, after 0)"


def _new_blocks(content: str) -> list[str]:
    return [re.sub(r"^\[\d+\]\s?", "", b) for b in blocks(content or "")]   # in case the model copied the [n] labels


def replace_blocks(s: Session, start: int, end: int, content: str) -> str:
    bs = blocks(s.content())
    start, end = int(start), int(end)
    if not 1 <= start <= end <= len(bs):
        return f"Error: the document has blocks 1 to {len(bs)}."
    bs[start - 1:end] = _new_blocks(content)
    s.write("\n\n".join(bs))
    return f"Done. The document now has {len(bs)} blocks."


def insert_blocks(s: Session, after: int, content: str) -> str:
    bs = blocks(s.content())
    new = _new_blocks(content)
    if not new:
        return "Error: content is empty."
    at = max(0, min(int(after), len(bs)))
    bs[at:at] = new
    s.write("\n\n".join(bs))
    return f"Done. The document now has {len(bs)} blocks."


def read_blocks(s: Session, start: int, end: int) -> str:
    bs = blocks(s.content())
    return "\n\n".join(f"[{i}] {bs[i - 1]}" for i in range(max(int(start), 1), min(int(end), len(bs)) + 1))[:READ_CHARS] or "No such blocks."


def write_cells(s: Session, start: str, rows: list) -> str:
    r0, c0 = sheet.cell_at(start)
    grid = s.content()
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        for j, v in enumerate(row if isinstance(row, list) else [row]):
            if v is None:
                continue
            while len(grid) <= r0 + i:
                grid.append([])
            line = grid[r0 + i]
            line.extend([None] * (c0 + j + 1 - len(line)))
            line[c0 + j] = sheet.number(v) if isinstance(v, str) else v
    s.write(grid)
    return "Done. These rows are now:\n" + "\n".join(sheet.as_lines(s.content(), r0, r0 + len(rows) - 1))[:READ_CHARS]


def _shift_rows(s: Session, row: int, count: int | None, sign: int) -> str:
    count = max(int(count or 1), 1)
    if int(row) < 1:
        return "Error: rows are numbered from 1."
    s.write(sheet.shift(s.content(), "row", int(row) - 1, sign * count))
    return f"Done: {count} row(s) {'inserted above' if sign > 0 else 'deleted from'} row {row}. The cell list in your instructions is updated."


def insert_rows(s: Session, row: int, count: int | None = None) -> str:
    return _shift_rows(s, row, count, 1)


def delete_rows(s: Session, row: int, count: int | None = None) -> str:
    return _shift_rows(s, row, count, -1)


def read_cells(s: Session, first: int, last: int) -> str:
    return "\n".join(sheet.as_lines(s.content(), int(first) - 1, int(last) - 1))[:READ_CHARS] or "Those rows are empty."


EDITS = {f.__name__: f for f in (replace_blocks, insert_blocks, read_blocks, write_cells, insert_rows, delete_rows, read_cells)}


def _shown(s: Session, selection: dict | None) -> tuple[str, str, bool]:
    """The file's content and the user's selection as the assistant sees them now, and whether the content was cut."""
    sel = selection or {}
    if s.kind == "sheet":
        lines = sheet.as_lines(s.content())
        fit, size = [], 0
        for line in lines:
            size += len(line)
            if size > SHEET_CHARS:
                break
            fit.append(line)
        more = f"\n({len(lines) - len(fit)} more rows are not shown: read them with read_cells)" if len(fit) < len(lines) else ""
        cell = sel.get("cell")
        return ("\n".join(fit) + more or "(the sheet is empty)",
                f"\n\nThe user's selected cell is {cell}." if isinstance(cell, str) and re.fullmatch(r"[A-Z]{1,2}\d+", cell) else "",
                bool(more))
    bs = blocks(s.content())
    note, whole = "", range(0)
    if isinstance(sel.get("start"), int) and isinstance(sel.get("end"), int) and 1 <= sel["start"] <= sel["end"] <= len(bs):
        where = f"block {sel['start']}" if sel["start"] == sel["end"] else f"blocks {sel['start']} to {sel['end']}"
        text = str(sel.get("text") or "").strip()[:1500]
        note = (f"\n\nThe user selected this text in {where}; \"this\" in the request means it:\n{text}" if text
                else f"\n\nThe user's cursor is in {where}.")
        whole = range(sel["start"], min(sel["end"], sel["start"] + 5) + 1)
    return numbered(bs, whole), note, sum(map(len, bs)) > DOC_CHARS


class Draft:
    """Follows a document edit while the model is still writing it, so the user watches the words arrive in the
    page instead of waiting for the finished change. The edit itself happens when the tool call is complete."""

    def __init__(self):
        self.sent: dict[tuple[int, int], int] = {}     # (step, tool call) -> characters of its text already sent

    def events(self, step: int, msg: dict) -> list[dict]:
        out = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            name, args = c["function"].get("name"), c["function"].get("arguments") or ""
            m = re.search(r'"content"\s*:\s*"', args)
            if name not in ("replace_blocks", "insert_blocks") or not m:
                continue
            at = {k: int(v) for k, v in re.findall(r'"(start|end|after)"\s*:\s*"?(\d+)"?\s*,', args[:m.start()])}
            if name == "insert_blocks" and "after" in at:
                at = {"start": at["after"] + 1, "end": at["after"]}       # nothing is replaced
            if not ("start" in at and "end" in at):
                continue                                                  # the position comes after the text: no preview
            text = _unfinished_string(args[m.end():])
            sent = self.sent.get((step, i), 0)
            if len(text) > sent:
                self.sent[step, i] = len(text)
                out.append({"type": "draft", "key": f"{step}.{i}", "start": at["start"], "end": at["end"], "text": text[sent:]})
        return out


def _unfinished_string(raw: str) -> str:
    """The text of a JSON string from just after its opening quote, whether or not it is finished yet."""
    end = re.match(r'(?:[^"\\]|\\.)*', raw, re.S).end()               # stops at the closing quote, if it is there
    raw = re.sub(r'\\(u[0-9a-fA-F]{0,3})?$', "", raw[:end])             # an escape still being written
    try:
        return json.loads(f'"{raw}"', strict=False)
    except ValueError:
        return ""


async def assist(file: dict, history: list[dict], selection: dict | None = None) -> AsyncIterator[dict]:
    """Streams events: draft (the words of a document edit as the model writes them), step (one per tool call),
    edit (the file changed: show it again), sources, token (the reply)."""
    s = Session(file)
    run = tools.Run()
    request = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")[:300]
    doc = s.kind == "doc"
    own = (DOC_TOOLS if doc else SHEET_TOOLS) + ([READ_BLOCKS if doc else READ_CELLS] if _shown(s, None)[2] else [])
    specs = tools.schemas(specs=own) + tools.schemas(READ_TOOLS) + mcp.schemas()
    allowed = {spec["function"]["name"]: set(spec["function"]["parameters"].get("properties", {})) for spec in specs}

    def system() -> str:
        content, note, _ = _shown(s, selection if not s.edits else None)   # block numbers of the selection are stale after an edit
        return SYSTEM.format(name=file["name"], what="document" if doc else "spreadsheet", today=date.today().isoformat(),
                             request=request, services=agent.SERVICES_NOTE if mcp.schemas() else "",
                             how=DOC_HOW if doc else SHEET_HOW, content=content, selection=note)

    async def call(name: str, args: dict) -> str:
        if name not in allowed:
            return f"Unknown tool {name}."
        if name not in EDITS:
            return await tools.call(run, name, args)
        try:
            return EDITS[name](s, **{k: v for k, v in args.items() if k in allowed[name]})
        except (ValueError, TypeError) as e:
            return f"Error: {e}"

    seen = 0
    async for e in agent.loop(system, history, specs, call, Draft().events if doc else None):
        if s.edits > seen or (e["type"] == "result" and e["tool"] in ("replace_blocks", "insert_blocks")):
            seen = s.edits              # also after a document edit that failed: its words were shown while it was written
            yield {"type": "edit"}
        if e["type"] == "token":
            yield {"type": "sources", "sources": run.sources}
        if e["type"] != "result":
            yield e
