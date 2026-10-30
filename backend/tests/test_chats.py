import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    dbmod.init()
    return TestClient(main.app)   # no context manager: the lifespan (model workers) isn't needed here


def test_chats_are_saved_listed_reopened_and_deleted(client):
    turns = [{"role": "user", "content": "Quando scade il contratto?"},
             {"role": "assistant", "content": "Il 31/12/2026 [1].", "sources": [{"n": 1, "kind": "doc", "page": 2}]}]
    assert client.put("/api/chats/a1", json={"title": "Quando scade il contratto?", "turns": turns[:1]}).status_code == 200
    client.put("/api/chats/a1", json={"title": "Quando scade il contratto?", "turns": turns})   # saved again after the answer
    client.put("/api/chats/b2", json={"title": "Second", "turns": turns[:1]})
    assert {c["id"] for c in client.get("/api/chats").json()} == {"a1", "b2"}
    assert client.get("/api/chats/a1").json()["turns"] == turns
    client.delete("/api/chats/a1")
    assert client.get("/api/chats/a1").status_code == 404
    assert [c["id"] for c in client.get("/api/chats").json()] == ["b2"]
