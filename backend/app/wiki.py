"""The "second brain": an office wiki compiled from documents (after Karpathy's LLM-wiki idea).

The model only *extracts* facts into a fixed JSON schema; code validates them against the source text,
resolves entities, and merges them. Prose is limited to short summaries regenerated from cited facts.
Pages are read and exported by wiki_views.py.
"""
import asyncio
import json
import logging
import re
import unicodedata
from datetime import date

from . import llm
from .db import db

log = logging.getLogger(__name__)

WINDOW_CHARS = 9000          # ~2.5k tokens of document text per extraction call
MAX_WINDOWS = 12
IDENTIFIERS = ("vat_id", "tax_code", "iban", "email", "phone")
HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "sig", "sigra", "signa", "dott", "dottssa", "ing", "avv", "geom", "rag"}
UNIT_ROLES = {"department", "dipartimento", "ufficio", "reparto", "team", "division", "divisione", "office", "unit"}
LEGAL_SUFFIXES = {"srl", "srls", "spa", "snc", "sas", "sapa", "scarl", "coop", "ltd", "llc", "inc", "gmbh", "sa",
                  "sarl", "bv", "ag", "plc", "co", "onlus", "ets"}
MONTHS = {1: ("gennaio", "january", "jan", "gen"), 2: ("febbraio", "february", "feb"), 3: ("marzo", "march", "mar"),
          4: ("aprile", "april", "apr"), 5: ("maggio", "may", "mag"), 6: ("giugno", "june", "jun", "giu"),
          7: ("luglio", "july", "jul", "lug"), 8: ("agosto", "august", "aug", "ago"),
          9: ("settembre", "september", "sep", "set"), 10: ("ottobre", "october", "oct", "ott"),
          11: ("novembre", "november", "nov"), 12: ("dicembre", "december", "dec", "dic")}

DEADLINE_KINDS = ["payment", "delivery", "submission", "expiry", "renewal", "cancellation", "meeting", "milestone"]
_DATE = {"type": ["string", "null"], "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
_NAMES = {"type": "array", "items": {"type": "string"}}
SCHEMA = {"type": "object", "required": ["summary", "doc_type", "doc_date", "entities", "relations", "events", "deadlines"],
          "properties": {
    "summary": {"type": "string"},
    "doc_type": {"enum": ["invoice", "contract", "quote", "order", "letter", "minutes", "policy", "report", "other"]},
    "doc_date": _DATE,
    "entities": {"type": "array", "items": {"type": "object", "required": ["type", "name", "role", "facts"], "properties": {
        "type": {"enum": ["organization", "person", "project", "place"]},
        "name": {"type": "string"},
        "role": {"type": ["string", "null"]},
        "facts": {"type": "array", "items": {"type": "object", "required": ["key", "value", "page"], "properties": {
            "key": {"enum": [*IDENTIFIERS, "address", "job_title", "note"]},
            "value": {"type": "string"}, "page": {"type": "integer"}}}}}}},
    "relations": {"type": "array", "items": {"type": "object", "required": ["from", "to", "label", "page"], "properties": {
        "from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"}, "page": {"type": "integer"}}}},
    "events": {"type": "array", "items": {"type": "object", "required": ["date", "what", "entities", "page"], "properties": {
        "date": _DATE, "what": {"type": "string"}, "entities": _NAMES, "page": {"type": "integer"}}}},
    "deadlines": {"type": "array", "items": {"type": "object", "required": ["kind", "date", "what", "entities", "page"], "properties": {
        "kind": {"enum": DEADLINE_KINDS + ["schedule"]},
        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}, "what": {"type": "string"},
        "entities": _NAMES, "page": {"type": "integer"}}}}}}

SYSTEM = """You maintain an office knowledge base. Read the document and fill the JSON.
- entities: the organizations (companies, public offices), people and named projects the document is about.
  NOT entities: products, services, invoice line items, departments or offices of the same company, the document itself.
  Places (cities, airports, stations, venues, addresses) get type "place"; an address of an entity is its "address" fact.
  role: this entity's single role in THIS document (e.g. "supplier", "client", "employee", "signatory"); null if unclear.
  facts: identifiers and contact data written in the text, copied exactly (vat_id, tax_code, iban, email, phone, address),
  job_title for people, note for other key facts about the entity.
- relations: links between two extracted entities (exact names), e.g. person "works at" organization.
- events: what the document records, with date if written. For invoices/orders/quotes: ONE event like
  "Invoice 2026/0417, total 3.233,00 EUR" linked to both parties.
- deadlines: only obligations or dates someone must act on (payment due, deliver by, submit by, expiry, renewal,
  cancellation notice, a scheduled meeting or milestone). An issue date is NOT a deadline.
  Times on tickets, timetables and itineraries (boarding, gate closing, departure, check-in) have kind "schedule".
  If the year is missing, use the document's year.
- Use only what is written; never guess. Dates as YYYY-MM-DD. Write summary and "what" in the document's language.
  page = page number of the fact. summary = 2 sentences about the document."""

SUMMARY_SYSTEM = """Write a 2-3 sentence overview of this entity for an office wiki, using ONLY the facts given.
Write in the same language as the facts. No preamble, no lists, no citations."""


# --- normalization (shared with wiki_views) ---

def fold(s: str) -> str:
    """Lowercase, no accents, punctuation -> spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9@]+", " ", s).split())


def name_norm(name: str) -> str:
    """Identity key for entity names: 'Officine Rossi S.r.l.' == 'officine rossi srl' == 'Officine Rossi'."""
    words = fold(name.replace(".", "")).split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def person_name(name: str) -> str:
    """'ROSSI, MARIO MR' -> 'Mario Rossi': drops titles, undoes 'Surname, Name', fixes ALL CAPS."""
    words = [w for w in name.replace(",", " , ").split() if fold(w.replace(".", "")) not in HONORIFICS]
    if "," in words:
        i = words.index(",")
        words = words[i + 1:] + words[:i]
    clean = " ".join(w for w in words if w != ",")
    return clean.title() if clean.isupper() else clean or name.strip()


def compact(value: str, key: str = "") -> str:
    v = re.sub(r"[\s.\-/()]", "", value.lower())
    return re.sub(r"^[a-z]{2}(?=\d{8,})", "", v) if key in ("vat_id", "iban", "") else v


def date_in_text(iso: str, text: str, year_optional: bool = False) -> bool:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return False
    low = text.lower()
    days, months = {f"{d.day:02d}", str(d.day)}, {f"{d.month:02d}", str(d.month)}
    years = {str(d.year), str(d.year)[2:]}
    numeric = [f"{dd}{sep}{mm}" for dd in days for mm in months for sep in "/-."]
    named = [f"{dd} {m}" for dd in days for m in MONTHS[d.month]]
    if iso in low:
        return True
    for stem in numeric:
        if any(f"{stem}{sep}{y}" in low for sep in "/-." for y in years):
            return True
    if any(re.search(rf"\b{re.escape(stem)}\b", low) for stem in named) and (year_optional or str(d.year) in low):
        return True
    return year_optional and any(re.search(rf"(?<!\d){re.escape(stem)}(?![\d/.-])", low) for stem in numeric)


def slugify(name: str) -> str:
    return fold(name).replace(" ", "-")[:60] or "entity"


# --- extraction ---

def _windows(pages: list[dict]) -> list[str]:
    out, cur = [], ""
    for p in pages:
        block = f"=== Page {p['n']} ===\n{p['text'][:WINDOW_CHARS]}\n\n"
        if cur and len(cur) + len(block) > WINDOW_CHARS:
            out.append(cur)
            cur = ""
        cur += block
    return (out + [cur] if cur.strip() else out)[:MAX_WINDOWS]


async def _extract(doc_name: str, window: str) -> dict:
    content = await llm.chat("assistant", [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Document: {doc_name}\n\n{window}"}],
        max_tokens=3000, response_format={"type": "json_schema", "json_schema": {"name": "extraction", "schema": SCHEMA}},
        **llm.NO_THINK)
    return json.loads(content)


async def _combine_summaries(parts: list[str]) -> str:
    return (await llm.chat("assistant", [{"role": "user", "content":
        "These are summaries of consecutive parts of one document. Write a single 2-sentence summary, "
        "in the same language:\n\n" + "\n".join(f"- {p}" for p in parts)}], max_tokens=200, **llm.NO_THINK)).strip()


# --- merge into the wiki ---

def _resolve(conn, etype: str, name: str, identifiers: dict[str, str]) -> tuple[int, bool]:
    """Finds the entity by alias or shared identifier (VAT/tax code), or creates it. Returns (id, created)."""
    raw, name = name, person_name(name) if etype == "person" else name
    norms = list(dict.fromkeys([name_norm(name), name_norm(raw)]))
    for n in norms:
        row = conn.execute("SELECT entity_id FROM aliases WHERE norm=?", (n,)).fetchone()
        if row:
            for other in norms:
                conn.execute("INSERT OR IGNORE INTO aliases(norm, entity_id) VALUES (?,?)", (other, row[0]))
            if name != raw:   # pages created before names were cleaned up
                conn.execute("UPDATE entities SET name=? WHERE id=? AND name=?", (name, row[0], raw.strip()))
            return row[0], False
    for key in ("vat_id", "tax_code"):
        if key in identifiers:
            row = conn.execute("SELECT entity_id FROM facts WHERE key=? AND norm=? AND entity_id IS NOT NULL AND status='ok'",
                               (key, identifiers[key])).fetchone()
            if row:
                for n in norms:
                    conn.execute("INSERT OR IGNORE INTO aliases(norm, entity_id) VALUES (?,?)", (n, row[0]))
                return row[0], False
    slug, i = slugify(name), 1
    while conn.execute("SELECT 1 FROM entities WHERE slug=?", (slug,)).fetchone():
        i += 1
        slug = f"{slugify(name)}-{i}"
    eid = conn.execute("INSERT INTO entities(type, name, slug) VALUES (?,?,?)", (etype, name.strip(), slug)).lastrowid
    for n in norms:
        conn.execute("INSERT OR IGNORE INTO aliases(norm, entity_id) VALUES (?,?)", (n, eid))
    return eid, True


def merge(conn, doc_id: str, n_pages: int, text: str, results: list[dict]) -> dict:
    """Validates extractions against the text and writes facts. Returns counts for the log."""
    text_fold = fold(text.replace(".", ""))
    text_compact = compact(text)
    ignored = {r[0] for r in conn.execute("SELECT norm FROM ignored_names")}
    rejected = {(r[0], r[1], r[2]) for r in conn.execute("SELECT entity_id, key, norm FROM rejected_facts")}
    page_of = lambda p: min(max(int(p or 1), 1), n_pages)
    ids: dict[str, int] = {}
    touched, created, seen, n_facts = set(), [], set(), 0

    def add(eid, kind, key, value, page, norm=None, dt=None, target=None):
        nonlocal n_facts
        norm = norm if norm is not None else fold(value)
        if eid is not None and (eid, key, norm) in rejected or (eid, kind, key, norm, dt, target) in seen:
            return
        seen.add((eid, kind, key, norm, dt, target))
        conn.execute("INSERT INTO facts(entity_id, doc_id, page, kind, key, value, norm, date, target_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (eid, doc_id, page_of(page), kind, key, value.strip(), norm, dt, target))
        n_facts += 1
        if eid is not None:
            touched.add(eid)

    def lookup(name: str) -> int | None:   # only entities extracted (and validated) from this document
        return ids.get(name_norm(name))

    for res in results:
        for ent in res.get("entities", []):
            n = name_norm(ent["name"])
            if ent["type"] == "place" or not n or n not in text_fold:      # must literally appear in the document
                continue
            if fold(ent.get("role") or "") in UNIT_ROLES:                    # a department of a company, not a party
                continue
            if {n, name_norm(person_name(ent["name"]))} & ignored:
                continue
            idents = {f["key"]: compact(f["value"], f["key"]) for f in ent["facts"] if f["key"] in IDENTIFIERS}
            idents = {k: v for k, v in idents.items() if v and v in text_compact}
            eid, new = _resolve(conn, ent["type"], ent["name"], idents)
            ids[n] = eid
            if ent["type"] == "person":
                ids.setdefault(name_norm(person_name(ent["name"])), eid)
            if new:
                created.append(conn.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[0])
            if ent.get("role"):
                add(eid, "role", "role", ent["role"], 1)
            for f in ent["facts"]:
                if f["key"] in IDENTIFIERS:
                    if f["key"] in idents:
                        add(eid, "attr", f["key"], f["value"], f["page"], norm=idents[f["key"]])
                elif f["value"].strip():
                    add(eid, "attr", f["key"], f["value"], f["page"])

    for res in results:
        for rel in res.get("relations", []):
            a, b = lookup(rel["from"]), lookup(rel["to"])
            if a and b and a != b:
                add(a, "relation", "relation", rel["label"], rel["page"], target=b)
        for kind in ("events", "deadlines"):
            for ev in res.get(kind, []):
                dt = ev.get("date") if date_in_text(ev.get("date"), text, year_optional=kind == "deadlines") else None
                if kind == "deadlines" and (not dt or ev.get("kind") not in DEADLINE_KINDS) or not ev["what"].strip():
                    continue
                targets = {lookup(name) for name in ev.get("entities", [])} - {None} or {None}
                for eid in targets:
                    add(eid, kind[:-1], kind[:-1], ev["what"], ev["page"], dt=dt)

    if touched:
        marks = ",".join("?" * len(touched))
        conn.execute(f"UPDATE entities SET summary_stale=1, updated_at=datetime('now') WHERE id IN ({marks})", list(touched))
    return {"facts": n_facts, "entities": sorted(ids.values()), "created": created}


async def refresh_summaries(ids: list[int] | None = None):
    from .wiki_views import entity_markdown
    with db() as conn:
        q = "SELECT id FROM entities WHERE summary_stale=1 AND summary_edited=0"
        stale = [r[0] for r in conn.execute(q) if ids is None or r[0] in ids]
        pages = {eid: entity_markdown(conn, eid, for_llm=True) for eid in stale}
    for eid, md in pages.items():
        try:
            summary = await llm.chat("assistant", [{"role": "system", "content": SUMMARY_SYSTEM},
                                                   {"role": "user", "content": md}], max_tokens=220, **llm.NO_THINK)
        except Exception:
            log.exception("summary failed for entity %s", eid)
            continue
        with db() as conn:
            conn.execute("UPDATE entities SET summary=?, summary_stale=0 WHERE id=? AND summary_edited=0", (summary.strip(), eid))


async def ingest(doc_id: str):
    with db() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if doc is None or doc["status"] != "ready":
            return
        pages = [dict(r) for r in conn.execute("SELECT n, text FROM pages WHERE doc_id=? ORDER BY n", (doc_id,))]
        conn.execute("UPDATE documents SET wiki_status='processing' WHERE id=?", (doc_id,))
    text = "\n\n".join(p["text"] for p in pages)
    results = [await _extract(doc["name"], w) for w in _windows(pages)]
    summaries = [r["summary"].strip() for r in results if r.get("summary", "").strip()]
    summary = summaries[0] if len(summaries) == 1 else (await _combine_summaries(summaries) if summaries else "")
    first = results[0] if results else {}
    doc_date = first.get("doc_date") if date_in_text(first.get("doc_date"), text) else None

    with db() as conn:
        conn.execute("DELETE FROM facts WHERE doc_id=?", (doc_id,))
        counts = merge(conn, doc_id, max(len(pages), 1), text, results)
        conn.execute("UPDATE documents SET summary=?, doc_type=?, doc_date=?, wiki_status='done' WHERE id=?",
                     (summary, first.get("doc_type"), doc_date, doc_id))
        new = f"; new: {', '.join(counts['created'])}" if counts["created"] else ""
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('ingest', ?)",
                     (f"{doc['name']}: {counts['facts']} facts, {len(counts['entities'])} entities{new}",))
        cleanup(conn)
    await refresh_summaries(counts["entities"])


def cleanup(conn):
    """Removes entities left without facts or notes (e.g. after their documents were deleted)."""
    conn.execute("""DELETE FROM entities WHERE notes = '' AND summary_edited = 0 AND NOT EXISTS
                    (SELECT 1 FROM facts f WHERE f.entity_id = entities.id AND f.status = 'ok')""")


# --- worker ---

_queue: asyncio.Queue[str] = asyncio.Queue()


def enqueue(doc_id: str):
    with db() as conn:
        conn.execute("UPDATE documents SET wiki_status='pending' WHERE id=?", (doc_id,))
    _queue.put_nowait(doc_id)


def resume_pending():
    with db() as conn:
        for r in conn.execute("SELECT id FROM documents WHERE status='ready' AND wiki_status IN ('pending','processing') ORDER BY created_at"):
            _queue.put_nowait(r["id"])


async def worker():
    while True:
        doc_id = await _queue.get()
        try:
            await ingest(doc_id)
        except Exception as e:
            with db() as conn:
                if conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
                    continue   # deleted while it was being read (e.g. removed from a synced folder): nothing to do
            log.exception("wiki ingest failed for %s", doc_id)
            with db() as conn:
                conn.execute("UPDATE documents SET wiki_status='error' WHERE id=?", (doc_id,))
                conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('error', ?)", (f"{doc_id}: {str(e)[:200]}",))
