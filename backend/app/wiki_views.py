"""Reading the wiki: entity pages, the review list (lint), chat context, and the Obsidian export."""
import io
import json
import re
import zipfile
from datetime import date, timedelta
from difflib import SequenceMatcher

from .db import db
from .wiki import fold, name_norm

CONFLICT_KEYS = ("vat_id", "tax_code", "iban")   # an entity can only have one of each
STOPWORDS = set("""il lo la i gli le un una di da del della dei delle in con su per tra fra che chi cosa come quando dove
quale quali quanto e o ma non al alla ai alle dal dalla nel nella sul sulla mi ci si the a an of to in on for with and or
what who when where which how is are was were do does did our my your their about from by""".split())
LABELS = {
    "it": {"organization": "Organizzazione", "person": "Persona", "project": "Progetto", "summary": "Sintesi",
           "data": "Dati", "links": "Collegamenti", "events": "Cronologia", "deadlines": "Scadenze",
           "documents": "Documenti", "notes": "Note", "index": "Indice", "review": "Da controllare",
           "vat_id": "Partita IVA", "tax_code": "Codice fiscale", "iban": "IBAN", "email": "Email", "phone": "Telefono",
           "address": "Indirizzo", "job_title": "Ruolo", "note": "Nota", "role": "Ruolo nei documenti"},
    "en": {"organization": "Organization", "person": "Person", "project": "Project", "summary": "Summary",
           "data": "Details", "links": "Links", "events": "Timeline", "deadlines": "Deadlines",
           "documents": "Documents", "notes": "Notes", "index": "Index", "review": "To review",
           "vat_id": "VAT number", "tax_code": "Tax code", "iban": "IBAN", "email": "Email", "phone": "Phone",
           "address": "Address", "job_title": "Job title", "note": "Note", "role": "Role in documents"},
}


def _facts(conn, eid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT f.id, f.kind, f.key, f.value, f.date, f.page, f.doc_id, d.name AS doc_name, f.target_id,
                  t.name AS target_name, t.slug AS target_slug
           FROM facts f JOIN documents d ON d.id = f.doc_id LEFT JOIN entities t ON t.id = f.target_id
           WHERE f.entity_id = ? AND f.status = 'ok' ORDER BY f.date IS NULL, f.date DESC, f.id""", (eid,))]


def entity_detail(conn, eid: int) -> dict | None:
    e = conn.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    if e is None:
        return None
    facts = _facts(conn, eid)
    backlinks = [dict(r) for r in conn.execute(
        """SELECT DISTINCT f.value AS label, s.id, s.name, s.slug, f.doc_id, f.page, d.name AS doc_name
           FROM facts f JOIN entities s ON s.id = f.entity_id JOIN documents d ON d.id = f.doc_id
           WHERE f.target_id = ? AND f.status = 'ok'""", (eid,))]
    docs = [dict(r) for r in conn.execute(
        """SELECT DISTINCT d.id, d.name, d.doc_type, d.doc_date, d.summary FROM facts f JOIN documents d ON d.id = f.doc_id
           WHERE f.entity_id = ? AND f.status = 'ok' ORDER BY d.doc_date DESC""", (eid,))]
    conflicts = {c["key"] for c in conflicts_for(conn, eid)}
    return {**dict(e), "facts": facts, "backlinks": backlinks, "documents": docs, "conflict_keys": sorted(conflicts)}


def _dedup(facts: list[dict], kind: str) -> list[dict]:
    seen, out = set(), []
    for f in facts:
        k = (f["key"], fold(f["value"]), f["date"]) if kind != "attr" else (f["key"], fold(f["value"]))
        if f["kind"] == kind and k not in seen:
            seen.add(k)
            out.append(f)
    return out


def entity_markdown(conn, eid: int, for_llm: bool = False, lang: str = "it") -> str:
    """One wiki page. for_llm: compact, with sources inline; otherwise Obsidian markdown with [[links]]."""
    L = LABELS[lang]
    e = entity_detail(conn, eid)
    src = lambda f: f"({f['doc_name']}, p. {f['page']})" if for_llm else f"([[{f['doc_name']}]], p. {f['page']})"
    link = lambda name: name if for_llm else f"[[{name}]]"
    lines = [f"# {e['name']}", f"{L[e['type']]}"]
    if e["summary"] and not for_llm:
        lines += ["", e["summary"]]
    roles = sorted({f["value"] for f in e["facts"] if f["kind"] == "role"})
    if roles:
        lines += ["", f"{L['role']}: {', '.join(roles)}"]
    attrs = _dedup(e["facts"], "attr")
    if attrs:
        lines += ["", f"## {L['data']}"] + [f"- {L.get(f['key'], f['key'])}: {f['value']} {src(f)}" for f in attrs]
    rels = [f for f in e["facts"] if f["kind"] == "relation"]
    if rels or e["backlinks"]:
        lines += ["", f"## {L['links']}"]
        lines += [f"- {f['value']} {link(f['target_name'])} {src(f)}" for f in rels if f["target_name"]]
        lines += [f"- {link(b['name'])} {b['label']} {src(b)}" for b in e["backlinks"]]
    for kind, title in (("deadline", L["deadlines"]), ("event", L["events"])):
        items = _dedup(e["facts"], kind)
        if items:
            lines += ["", f"## {title}"] + [f"- {f['date'] or 'n/d'}: {f['value']} {src(f)}" for f in items]
    if e["notes"].strip():
        lines += ["", f"## {L['notes']}", e["notes"].strip()]
    if not for_llm and e["documents"]:
        lines += ["", f"## {L['documents']}"] + [f"- [[{d['name']}]]" for d in e["documents"]]
    return "\n".join(lines)


# --- lint: conflicts and likely duplicates, computed live so they are always current ---

def conflicts_for(conn, eid: int | None = None) -> list[dict]:
    q = f"""SELECT f.entity_id, e.name, f.key, f.id, f.value, f.norm, f.doc_id, d.name AS doc_name, f.page
            FROM facts f JOIN entities e ON e.id = f.entity_id JOIN documents d ON d.id = f.doc_id
            WHERE f.status='ok' AND f.key IN ({','.join('?' * len(CONFLICT_KEYS))})""" + (" AND f.entity_id = ?" if eid else "")
    groups: dict[tuple, list] = {}
    for r in conn.execute(q, (*CONFLICT_KEYS, *([eid] if eid else []))):
        groups.setdefault((r["entity_id"], r["key"]), []).append(dict(r))
    out = []
    for (entity_id, key), rows in groups.items():
        by_value: dict[str, dict] = {}
        for r in rows:
            opt = by_value.setdefault(r["norm"], {"value": r["value"], "fact_ids": [], "sources": []})
            opt["fact_ids"].append(r["id"])
            opt["sources"].append({"doc_id": r["doc_id"], "doc_name": r["doc_name"], "page": r["page"]})
        if len(by_value) > 1:
            out.append({"kind": "conflict", "entity_id": entity_id, "name": rows[0]["name"], "key": key,
                        "options": list(by_value.values())})
    return out


def duplicates(conn) -> list[dict]:
    ents = [dict(r) for r in conn.execute("SELECT id, type, name FROM entities")]
    dismissed = {(r[0], r[1]) for r in conn.execute("SELECT a, b FROM not_duplicates")}
    shared_ids = {(r[0], r[1]) for r in conn.execute(
        """SELECT a.entity_id, b.entity_id FROM facts a JOIN facts b
           ON a.key = b.key AND a.norm = b.norm AND a.entity_id < b.entity_id
           WHERE a.key IN ('vat_id','tax_code') AND a.status='ok' AND b.status='ok'""")}
    out = []
    for i, a in enumerate(ents):
        na = name_norm(a["name"])
        for b in ents[i + 1:]:
            pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if pair in dismissed or a["type"] != b["type"]:
                continue
            nb = name_norm(b["name"])
            short, long_ = sorted((na, nb), key=len)
            contained = len(short) >= 4 and re.search(rf"\b{re.escape(short)}\b", long_)
            if pair in shared_ids or contained or SequenceMatcher(None, na, nb).ratio() >= 0.85:
                out.append({"kind": "duplicate", "a": {"id": a["id"], "name": a["name"]}, "b": {"id": b["id"], "name": b["name"]},
                            "same_id": pair in shared_ids})
    return out


def review_items(conn) -> list[dict]:
    return conflicts_for(conn) + duplicates(conn)


def deadlines(conn, past_days: int = 30) -> list[dict]:
    since = (date.today() - timedelta(days=past_days)).isoformat()
    rows = conn.execute(
        """SELECT f.date, f.value, f.doc_id, d.name AS doc_name, f.page,
                  json_group_array(DISTINCT json_object('id', e.id, 'name', e.name)) FILTER (WHERE e.id IS NOT NULL) AS ents
           FROM facts f JOIN documents d ON d.id = f.doc_id LEFT JOIN entities e ON e.id = f.entity_id
           WHERE f.kind='deadline' AND f.status='ok' AND f.date >= ?
           GROUP BY f.date, f.value, f.doc_id ORDER BY f.date""", (since,))
    today = date.today().isoformat()
    return [{**{k: r[k] for k in ("date", "value", "doc_id", "doc_name", "page")}, "overdue": r["date"] < today,
             "entities": json.loads(r["ents"] or "[]")}
            for r in rows]


def overview(conn) -> dict:
    ents = [dict(r) for r in conn.execute(
        """SELECT e.id, e.type, e.name, e.slug, e.summary, e.updated_at,
                  (SELECT COUNT(DISTINCT doc_id) FROM facts f WHERE f.entity_id = e.id AND f.status='ok') AS doc_count
           FROM entities e ORDER BY e.name COLLATE NOCASE""")]
    pending = conn.execute("SELECT COUNT(*) FROM documents WHERE status='ready' AND wiki_status IN ('pending','processing')").fetchone()[0]
    return {"entities": ents, "review": review_items(conn), "deadlines": deadlines(conn), "pending_docs": pending,
            "log": [dict(r) for r in conn.execute("SELECT ts, action, detail FROM wiki_log ORDER BY id DESC LIMIT 15")]}


# --- chat: wiki pages relevant to a question ---

DEADLINE_WORDS = re.compile(r"scaden|deadline|entro quando|due date|in scadenza|da rinnovare|rinnov|expir", re.I)


def context_for(question: str, limit: int = 3) -> list[dict]:
    q = fold(question)
    q_words = {w for w in q.split() if len(w) >= 4 and w not in STOPWORDS}
    with db() as conn:
        scored = []
        for e in conn.execute("SELECT id, name FROM entities"):
            names = [name_norm(e["name"])] + [r[0] for r in conn.execute("SELECT norm FROM aliases WHERE entity_id=?", (e["id"],))]
            full = any(re.search(rf"\b{re.escape(n)}\b", q) for n in names if len(n) >= 3)
            overlap = len(q_words & {w for n in names for w in n.split()})
            if full or overlap:
                scored.append((full * 10 + overlap, e["id"], e["name"]))
        pages = [{"kind": "wiki", "entity_id": eid, "name": name, "text": entity_markdown(conn, eid, for_llm=True)}
                 for _, eid, name in sorted(scored, reverse=True)[:limit]]
        if DEADLINE_WORDS.search(question):
            items = deadlines(conn, past_days=60)
            if items:
                text = "\n".join(f"- {d['date']}{' (overdue)' if d['overdue'] else ''}: {d['value']} "
                                 f"[{', '.join(x['name'] for x in d['entities'])}] ({d['doc_name']}, p. {d['page']})" for d in items)
                pages.append({"kind": "wiki", "entity_id": None, "name": "Scadenze / Deadlines", "text": text})
    return pages


# --- export for Obsidian ---

def export_zip(lang: str = "it") -> bytes:
    L = LABELS[lang]
    buf = io.BytesIO()
    safe = lambda s: re.sub(r'[\\/:*?"<>|#^\[\]]', "-", s).strip()
    with db() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ents = conn.execute("SELECT id, type, name, summary, updated_at FROM entities ORDER BY type, name COLLATE NOCASE").fetchall()
        index = [f"# {L['index']}", ""]
        for etype in ("organization", "person", "project"):
            group = [e for e in ents if e["type"] == etype]
            if group:
                index += [f"## {L[etype]}"] + [
                    f"- [[{e['name']}]]: {(e['summary'].split('. ')[0].rstrip('.') + '.') if e['summary'] else ''} ({e['updated_at'][:10]})"
                    for e in group] + [""]
            for e in group:
                z.writestr(f"{L[etype]}/{safe(e['name'])}.md", entity_markdown(conn, e["id"], lang=lang))
        docs = conn.execute("SELECT name, doc_type, doc_date, summary FROM documents WHERE status='ready' ORDER BY name").fetchall()
        index += [f"## {L['documents']}"] + [f"- [[{d['name']}]]" for d in docs]
        for d in docs:
            z.writestr(f"{L['documents']}/{safe(d['name'])}.md",
                       f"# {d['name']}\n{d['doc_type'] or ''} {d['doc_date'] or ''}\n\n{d['summary']}\n")
        z.writestr("index.md", "\n".join(index))
        dl = deadlines(conn, past_days=3650)
        z.writestr(f"{L['deadlines']}.md", f"# {L['deadlines']}\n\n" + "\n".join(
            f"- {d['date']}: {d['value']} " + " ".join(f"[[{x['name']}]]" for x in d["entities"]) + f" ([[{d['doc_name']}]])"
            for d in dl))
        z.writestr("log.md", "# Log\n\n" + "\n".join(
            f"## [{r['ts'][:10]}] {r['action']} | {r['detail']}" for r in conn.execute("SELECT * FROM wiki_log ORDER BY id")))
    return buf.getvalue()
