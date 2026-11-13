"""SQLite storage: documents, per-page text, and chunks indexed twice (FTS5 keywords + sqlite-vec vectors)."""
import json
import sqlite3
import struct
import platform
import sys
from contextlib import contextmanager

try:
    import sqlite_vec
except ImportError:  # No sqlite-vec wheel exists for Windows ARM64.
    if sys.platform != "win32" or platform.machine().upper() != "ARM64":
        raise
    sqlite_vec = None

from .config import DATA_DIR, EMBED_DIM

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  ext TEXT NOT NULL,
  size INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'queued',      -- queued | processing | ready | error
  pages_total INTEGER NOT NULL DEFAULT 0,
  pages_done INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  n INTEGER NOT NULL,
  text TEXT NOT NULL,
  method TEXT NOT NULL,                       -- text (born-digital) | ocr
  has_image INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (doc_id, n)
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
  DELETE FROM vec_chunks WHERE rowid = old.id;
END;
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- wiki ("second brain"): entities compiled from documents; every fact cites a document page
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,                          -- organization | person | project
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  summary TEXT NOT NULL DEFAULT '',
  summary_edited INTEGER NOT NULL DEFAULT 0,   -- 1 = written by a person, never regenerated
  summary_stale INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS aliases (
  norm TEXT PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,  -- NULL: about the document itself
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  kind TEXT NOT NULL,                          -- attr | role | relation | event | deadline
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  norm TEXT NOT NULL,
  date TEXT,
  target_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'ok'            -- ok | rejected
);
CREATE INDEX IF NOT EXISTS facts_entity ON facts(entity_id);
CREATE INDEX IF NOT EXISTS facts_doc ON facts(doc_id);
CREATE TABLE IF NOT EXISTS rejected_facts (entity_id INTEGER, key TEXT, norm TEXT, PRIMARY KEY (entity_id, key, norm));
CREATE TABLE IF NOT EXISTS ignored_names (norm TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS not_duplicates (a INTEGER, b INTEGER, PRIMARY KEY (a, b));
-- connected sources: local folders, synced into documents
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                          -- always folder
  name TEXT NOT NULL,
  config TEXT NOT NULL,                        -- JSON: {path}
  status TEXT NOT NULL DEFAULT 'idle',         -- idle | syncing | error
  error TEXT,
  last_sync TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  turns TEXT NOT NULL,                         -- JSON: messages with their sources, as shown in the UI
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- the Desk: files the user and the assistant edit in the app
CREATE TABLE IF NOT EXISTS desk_files (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                          -- doc (content: Markdown) | sheet (JSON rows of cells) | slides (JSON list of slides)
  content TEXT NOT NULL,
  previous TEXT,                               -- content before the assistant's last run of changes, for undo
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wiki_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  action TEXT NOT NULL,                        -- ingest | merge | resolve | reject | delete | edit
  detail TEXT NOT NULL
);
"""
MIGRATIONS = [  # columns added after the first release; ALTER fails harmlessly once applied
    "ALTER TABLE documents ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN doc_type TEXT",
    "ALTER TABLE documents ADD COLUMN doc_date TEXT",
    "ALTER TABLE documents ADD COLUMN wiki_status TEXT NOT NULL DEFAULT 'pending'",  # pending|processing|done|error
    "ALTER TABLE documents ADD COLUMN source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL",
    "ALTER TABLE documents ADD COLUMN source_key TEXT",     # folder: relative path
    "ALTER TABLE documents ADD COLUMN source_mtime REAL",
]
VEC_TABLE = "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{dim}] distance_metric=cosine)"
if sqlite_vec is None:
    VEC_TABLE = "CREATE TABLE IF NOT EXISTS vec_chunks (rowid INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DIR / "office.db", timeout=30)
    conn.row_factory = sqlite3.Row
    if sqlite_vec is not None:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        conn.execute(VEC_TABLE.format(dim=get_setting(conn, "embed_dim", EMBED_DIM)))


def recreate_vec_table(conn: sqlite3.Connection, dim: int):
    conn.execute("DROP TABLE IF EXISTS vec_chunks")
    conn.execute(VEC_TABLE.format(dim=dim))


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


def vec(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def nearest(conn, vector: list[float], k: int, doc_ids: list[str] | None = None):
    """Cosine search, using a native extension where available or exact NumPy on ARM64."""
    if sqlite_vec is not None:
        return conn.execute("SELECT rowid FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                            (vec(vector), k * (4 if doc_ids else 1))).fetchall()
    import numpy as np
    sql = "SELECT v.rowid, v.embedding FROM vec_chunks v JOIN chunks c ON c.id=v.rowid"
    params = []
    if doc_ids:
        sql += " WHERE c.doc_id IN (" + ",".join("?" for _ in doc_ids) + ")"
        params = doc_ids
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    matrix = np.stack([np.frombuffer(row[1], dtype=np.float32) for row in rows])
    query = np.asarray(vector, dtype=np.float32)
    scores = (matrix @ query) / np.maximum(np.linalg.norm(matrix, axis=1) * np.linalg.norm(query), 1e-12)
    return [(rows[i][0],) for i in np.argsort(-scores, kind="stable")[:k]]
