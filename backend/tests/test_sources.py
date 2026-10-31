import os

import pytest

from app import db as dbmod
from app import ingest, sources


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ingest, "FILES", tmp_path / "files")
    monkeypatch.setattr(ingest, "PAGES", tmp_path / "pages")
    monkeypatch.setattr(ingest, "enqueue", lambda doc_id: None)   # no models in tests
    (tmp_path / "files").mkdir()
    dbmod.init()
    return tmp_path


def test_folder_sync_adds_updates_and_removes(env):
    root = env / "Clienti"
    (root / "2026").mkdir(parents=True)
    (root / "2026" / "offerta.txt").write_text("Offerta per Acme")
    (root / "note.md").write_text("Note")
    (root / ".hidden.txt").write_text("skip")
    (root / "~$lock.docx").write_text("skip")            # Office lock file
    (root / "photo.raw").write_text("unsupported")
    sid = sources.create("Clienti", {"path": str(root)})
    src = lambda: sources.get(dbmod._connect(), sid)

    assert sources.sync_folder(src()) == {"added": 2, "changed": 0, "removed": 0, "cited": []}
    assert sources.sync_folder(src())["added"] == 0                     # nothing new the second time
    (root / "note.md").write_text("Note aggiornate")
    os.utime(root / "note.md", (1e9, 1e9))
    (root / "2026" / "offerta.txt").unlink()
    assert sources.sync_folder(src()) == {"added": 0, "changed": 1, "removed": 1, "cited": []}
    with dbmod.db() as conn:
        [(doc_id, key)] = conn.execute("SELECT id, source_key FROM documents").fetchall()
    assert key == "note.md" and ingest.file_path(doc_id, ".md").read_text() == "Note aggiornate"


def test_missing_folder_is_an_error_not_a_wipe(env):
    sid = sources.create("USB", {"path": str(env / "gone")})
    with pytest.raises(RuntimeError, match="folder not found"):
        sources.sync_folder(sources.get(dbmod._connect(), sid))
