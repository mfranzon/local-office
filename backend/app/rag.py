"""Hybrid retrieval (vector + keyword, fused with RRF) and grounded answers with numbered citations."""
import re

from . import llm
from .db import db, vec

CANDIDATES = 40
RRF_K = 60

SYSTEM_DOCS = """You are an office assistant. Answer using ONLY the numbered excerpts below: "Wiki" excerpts are
knowledge-base pages compiled from the user's documents (good for overviews and history), the others are document pages
(good for exact details).
Cite every fact with its excerpt number in square brackets, e.g. [2]. If the excerpts don't contain the answer, say so plainly.
Reply in the same language as the user's question. Be concise; use lists or tables when they help.

{context}"""
SYSTEM_PLAIN = "You are a helpful office assistant. Reply in the same language as the user. Be concise."


def _fts_query(q: str) -> str | None:
    words = [w for w in re.findall(r"\w[\w./-]*", q) if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words) or None


async def search(question: str, doc_ids: list[str] | None = None, k: int = 8) -> list[dict]:
    [qv] = await llm.embed([question], query=True)
    ranks: dict[int, float] = {}
    with db() as conn:
        vec_rows = conn.execute(
            "SELECT rowid FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (vec(qv), CANDIDATES * (4 if doc_ids else 1))).fetchall()
        fts_rows = []
        if fq := _fts_query(question):
            fts_rows = conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                                    (fq, CANDIDATES)).fetchall()
        for rows in (vec_rows, fts_rows):
            for rank, r in enumerate(rows):
                ranks[r[0]] = ranks.get(r[0], 0) + 1 / (RRF_K + rank)
        if not ranks:
            return []
        ids = sorted(ranks, key=ranks.get, reverse=True)
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT c.id, c.doc_id, c.page, c.text, d.name FROM chunks c JOIN documents d ON d.id = c.doc_id "
            f"WHERE c.id IN ({marks})", ids)}
    hits = [rows[i] for i in ids if i in rows and (not doc_ids or rows[i]["doc_id"] in doc_ids)]
    return hits[:k]


def _label(source: dict) -> str:
    return f"Wiki: {source['name']}" if source.get("kind") == "wiki" else f"{source['name']}, p. {source['page']}"


def build_messages(history: list[dict], hits: list[dict] | None) -> list[dict]:
    if hits is None:
        system = SYSTEM_PLAIN
    else:
        context = "\n\n".join(f"[{i}] ({_label(h)})\n{h['text']}" for i, h in enumerate(hits, 1))
        system = SYSTEM_DOCS.format(context=context or "(no matching excerpts)")
    return [{"role": "system", "content": system}, *history[-8:]]
