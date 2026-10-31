"""Connected sources: local folders, kept in sync with the documents (read-only).

Folders are scanned for supported files: new files are added, changed ones read again, deleted ones removed.
Each folder syncs every few minutes and on demand.
"""
import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path

from . import ingest, wiki
from .db import db

log = logging.getLogger(__name__)

INTERVAL = 5 * 60
MAX_FILE_MB = 200
pick_folder: Callable[[], str | None] | None = None   # set by the desktop app: the native folder dialog
_locks: dict[int, asyncio.Lock] = {}


def get(conn, source_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return {**dict(row), "config": json.loads(row["config"])} if row else None


def listing() -> list[dict]:
    with db() as conn:
        rows = conn.execute("""SELECT s.*, (SELECT COUNT(*) FROM documents d WHERE d.source_id = s.id) AS doc_count
                               FROM sources s ORDER BY s.created_at""")
        return [{**dict(r), "config": json.loads(r["config"])} for r in rows]


def create(name: str, config: dict) -> int:
    with db() as conn:
        return conn.execute("INSERT INTO sources(kind, name, config) VALUES ('folder',?,?)",
                            (name, json.dumps(config))).lastrowid


def delete(source_id: int) -> list[int]:
    """Removes the source and the documents it brought in. Returns the wiki entities they cited."""
    with db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE source_id=?", (source_id,))]
        cited = ingest.remove(conn, ids)
        conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    return cited


# --- folders ---

def scan(root: Path) -> dict[str, tuple[Path, float]]:
    """Supported files under root by relative path, skipping hidden files and folders (e.g. .git, .DS_Store)."""
    out = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part.startswith((".", "~$")) for part in rel.parts) or p.suffix.lower() not in ingest.SUPPORTED:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and st.st_size <= MAX_FILE_MB * 2**20:
            out[rel.as_posix()] = (p, st.st_mtime)
    return out


def sync_folder(src: dict) -> dict:
    root = Path(src["config"]["path"])
    if not root.is_dir():
        raise RuntimeError("folder not found (moved, renamed or on a disconnected drive)")
    files = scan(root)
    with db() as conn:
        known = {r["source_key"]: (r["id"], r["ext"], r["source_mtime"]) for r in conn.execute(
            "SELECT id, ext, source_key, source_mtime FROM documents WHERE source_id=?", (src["id"],))}
    added = changed = 0
    for rel, (path, mtime) in files.items():
        if rel not in known:
            ingest.add(path.name, path.suffix.lower(), src=path, source_id=src["id"], source_key=rel, mtime=mtime)
            added += 1
        elif known[rel][2] != mtime:
            ingest.replace(known[rel][0], known[rel][1], path, mtime)
            changed += 1
    gone = [doc_id for rel, (doc_id, _, _) in known.items() if rel not in files]
    cited = []
    if gone:
        with db() as conn:
            cited = ingest.remove(conn, gone)
    return {"added": added, "changed": changed, "removed": len(gone), "cited": cited}


# --- scheduling ---

async def sync(source_id: int):
    lock = _locks.setdefault(source_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        with db() as conn:
            src = get(conn, source_id)
            if src is None:
                return
            conn.execute("UPDATE sources SET status='syncing', error=NULL WHERE id=?", (source_id,))
        try:
            result = await asyncio.to_thread(sync_folder, src)
        except Exception as e:
            log.warning("sync of source %s failed: %s", source_id, e)
            with db() as conn:
                conn.execute("UPDATE sources SET status='error', error=?, last_sync=datetime('now') WHERE id=?",
                             (str(e)[:300], source_id))
            return
        with db() as conn:
            conn.execute("UPDATE sources SET status='idle', last_sync=datetime('now') WHERE id=?", (source_id,))
        if result["cited"]:
            await wiki.refresh_summaries(result["cited"])


async def worker():
    while True:
        with db() as conn:
            due = [r["id"] for r in conn.execute("SELECT id, last_sync FROM sources") if r["last_sync"] is None or
                   conn.execute("SELECT (julianday('now') - julianday(?)) * 86400 > ?",
                                (r["last_sync"], INTERVAL)).fetchone()[0]]
        for source_id in due:
            await sync(source_id)
        await asyncio.sleep(30)
