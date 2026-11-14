"""The assistant on an OpenAI-compatible server the user runs on the same computer (Lucebox, vLLM…). HTTP is mocked."""
import asyncio
import json

import httpx
import pytest

from app import db as dbmod
from app import models


def serve(monkeypatch, reply="47815"):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "/models/Qwen3.8-27B-UD-IQ4_XS.gguf"}]})
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))


async def run_switch(role, ref):
    return [e async for e in models.switch(role, ref)]


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(models, "SNAPDRAGON", False)
    monkeypatch.setattr(models, "_active", {})
    dbmod.init()


@pytest.mark.parametrize("ref", ["http://127.0.0.1:8080", "http://localhost:8080/v1/", "http://[::1]:8080"])
def test_switch_to_a_local_server_uses_it_without_starting_llama(app_db, monkeypatch, ref):
    serve(monkeypatch)
    released = []
    monkeypatch.setattr(models.runtime, "release", released.append)
    monkeypatch.setattr(models.runtime, "ensure", lambda *a: pytest.fail("llama-server must not start"))
    events = asyncio.run(run_switch("assistant", ref))
    assert events[-1]["type"] == "switched" and events[-1]["model"]["name"] == "Qwen3.8-27B-UD-IQ4_XS.gguf"
    assert released == ["assistant"]
    assert asyncio.run(models.url("assistant")) == ref.rstrip("/").removesuffix("/v1") + "/v1"
    models._active.clear()
    models.load()                                   # survives a restart
    assert models.active("assistant").provider == "server" and models.active("assistant").downloaded


@pytest.mark.parametrize("role,ref,error", [
    ("assistant", "http://192.168.1.20:8080", "must run on this computer"),
    ("assistant", "https://api.openai.com/v1", "must run on this computer"),
    ("embed", "http://127.0.0.1:8080", "only the assistant"),
])
def test_other_machines_and_other_roles_are_refused(app_db, monkeypatch, role, ref, error):
    serve(monkeypatch)
    with pytest.raises(RuntimeError, match=error):
        asyncio.run(run_switch(role, ref))


def test_a_server_that_fails_the_role_test_is_not_adopted(app_db, monkeypatch):
    serve(monkeypatch, reply="I can't see images")
    models.load()
    before = models.active("assistant")
    with pytest.raises(RuntimeError, match="could not read a test image"):
        asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    assert models.active("assistant") == before


def test_hugging_face_links_are_still_models_not_servers():
    assert not models.is_server("https://huggingface.co/bartowski/Model-GGUF")
    assert not models.is_server("hf.co/bartowski/Model-GGUF:Q4_K_M")
    assert not models.is_server("bartowski/Model-GGUF")


def serve_many(monkeypatch, key=None, seen=None):
    """A LocalAI-like server: several models, each request must name one, optionally behind an API key."""
    def respond(request: httpx.Request) -> httpx.Response:
        if key and request.headers.get("authorization") != f"Bearer {key}":
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "whisper-1"}, {"id": "qwen3-vl-4b"}, {"id": "bge-m3"}]})
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body.get("model"))
        if body.get("model") != "qwen3-vl-4b":
            return httpx.Response(500, json={"error": {"message": "model is not a chat model"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "47815"}}]})
    monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))


def test_a_server_with_several_models_needs_one_picked(app_db, monkeypatch):
    serve_many(monkeypatch)
    with pytest.raises(RuntimeError, match=r"several models.*http://127.0.0.1:8080#whisper-1.*qwen3-vl-4b"):
        asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    with pytest.raises(RuntimeError, match="no model 'qwen9'"):
        asyncio.run(run_switch("assistant", "http://127.0.0.1:8080#qwen9"))


def test_the_picked_model_and_api_key_go_with_every_request(app_db, monkeypatch):
    seen = []
    serve_many(monkeypatch, key="sk-local", seen=seen)
    with pytest.raises(RuntimeError, match="wants an API key"):
        asyncio.run(run_switch("assistant", "http://127.0.0.1:8080#qwen3-vl-4b"))
    events = asyncio.run(run_switch("assistant", "http://sk-local@127.0.0.1:8080#qwen3-vl-4b"))
    view = events[-1]["model"]
    assert view["name"] == "qwen3-vl-4b" and view["ref"] == "http://127.0.0.1:8080/v1#qwen3-vl-4b" and "key" not in view
    models._active.clear()
    models.load()                                   # model and key survive a restart
    assert "sk-local" not in json.dumps(models.overview()["assistant"])
    from app import llm
    monkeypatch.setattr(llm, "_http", models._http)
    assert asyncio.run(llm.chat("assistant", [{"role": "user", "content": "hi"}])) == "47815"
    assert seen == ["qwen3-vl-4b", "qwen3-vl-4b"]


def test_thinking_is_turned_off_with_reasoning_effort_unless_the_server_rejects_it(app_db, monkeypatch):
    for strict in (False, True):                    # LocalAI accepts "none"; a strict server only knows low/medium/high
        bodies = []

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "qwen3.5-9b"}]})
            body = json.loads(request.content)
            bodies.append(body)
            if strict and "reasoning_effort" in body:
                return httpx.Response(400, json={"error": {"message": "reasoning_effort: Input should be 'low', 'medium' or 'high'"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "47815"}}]})
        monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        events = asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
        assert events[-1]["type"] == "switched"
        assert models.active("assistant").effort_none is not strict
        assert bodies[-1].get("reasoning_effort") == (None if strict else "none")
