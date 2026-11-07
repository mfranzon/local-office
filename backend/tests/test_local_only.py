import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    dbmod.init()
    return TestClient(main.app, base_url="http://127.0.0.1:8123")


@pytest.mark.parametrize("host", ["127.0.0.1:8123", "localhost:8123", "[::1]:8123", "localhost"])
def test_loopback_hosts_are_served(client, host):
    assert client.get("/api/chats", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example:8123", "evil.example", "127.0.0.1.evil.example:8123", ""])
def test_other_hosts_are_refused(client, host):
    # DNS rebinding: the request reaches 127.0.0.1 but carries the attacker's hostname
    assert client.get("/api/chats", headers={"host": host}).status_code == 403


def test_same_origin_requests_are_served(client):
    assert client.put("/api/chats/a1", json={"title": "t", "turns": []},
                      headers={"origin": "http://127.0.0.1:8123"}).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:9999", "http://localhost:8123"])
def test_cross_origin_requests_are_refused(client, origin):
    # CSRF: a page on another origin posts a form, which needs no CORS preflight
    response = client.post("/api/skills/import", headers={"origin": origin},
                           files={"file": ("SKILL.md", b"---\nname: x\ndescription: y\n---\nhi", "text/markdown")})
    assert response.status_code == 403
