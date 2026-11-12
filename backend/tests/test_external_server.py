"""The assistant on an OpenAI-compatible server the user runs on the same computer (Lucebox, vLLM…). HTTP is mocked."""
import asyncio
import json

import httpx
import pytest

from app import db as dbmod
from app import llm, models


def serve(monkeypatch, reply="47815"):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "/models/Qwen3.8-27B-UD-IQ4_XS.gguf"}]})
        assert request.url.path == "/v1/chat/completions"
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


# --- colibri: thinking is switched off with a top-level enable_thinking, and token penalties are refused ---

def colibri(monkeypatch, strict: bool, seen: list):
    """strict: refuses fields it doesn't know. Otherwise it ignores chat_template_kwargs and thinks."""
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-27b-colibri"}]})
        body = json.loads(request.content)
        seen.append(body)
        if "repetition_penalty" in body or (strict and "chat_template_kwargs" in body):
            return httpx.Response(400, json={"error": {"message": "unsupported field"}})
        message = {"content": "47815"}
        if body.get("enable_thinking") is not False:
            message["reasoning_content"] = "The image shows a code…"
        return httpx.Response(200, json={"choices": [{"message": message}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(models, "_http", client)
    monkeypatch.setattr(llm, "_http", client)
    monkeypatch.setattr(llm, "SNAPDRAGON", False)
    monkeypatch.setattr(models.runtime, "release", lambda role: None)


@pytest.mark.parametrize("strict", [True, False])
def test_colibri_gets_its_own_thinking_switch_the_model_name_and_no_penalties(app_db, monkeypatch, strict):
    seen = []
    colibri(monkeypatch, strict, seen)
    asyncio.run(run_switch("assistant", "http://127.0.0.1:8000"))
    spec = models.active("assistant")
    assert spec.flags == ("enable_thinking",) and spec.ref == "http://127.0.0.1:8000/v1"
    seen.clear()

    async def ask():
        await llm.complete("assistant", [{"role": "user", "content": "hi"}], **llm.NO_THINK)
        return [t async for t in llm.chat_stream([{"role": "user", "content": "hi"}], repetition_penalty=1.0)]
    asyncio.run(ask())
    assert len(seen) == 2
    for body in seen:
        assert body["enable_thinking"] is False and body["model"] == "qwen3.8-27b-colibri"
        assert "chat_template_kwargs" not in body and "repetition_penalty" not in body


def test_a_server_that_understands_chat_template_kwargs_keeps_them(app_db, monkeypatch):
    serve(monkeypatch)
    monkeypatch.setattr(models.runtime, "release", lambda role: None)
    asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    spec = models.active("assistant")
    assert spec.flags == ()
    assert llm.body_for(spec, {"messages": [], **llm.NO_THINK}) == {
        "messages": [], **llm.NO_THINK, "model": "/models/Qwen3.8-27B-UD-IQ4_XS.gguf"}


def test_bundled_llama_requests_are_unchanged():
    body = {"messages": [], "repetition_penalty": 1.0, **llm.NO_THINK}
    assert llm.body_for(models.LLAMA_CATALOG[0], body) == body
