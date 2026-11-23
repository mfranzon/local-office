"""The assistant on an OpenAI-compatible server the user runs on the same computer (Lucebox, vLLM…). HTTP is mocked."""
import asyncio
import json

import httpx
import pytest

from app import db as dbmod
from app import engines, models


def answer(body: dict, content: str = "47815", **message) -> httpx.Response:
    """A chat reply, streamed if the request asks for it (ending with a usage-only chunk, as some servers do)."""
    if body.get("stream"):
        chunks = [{"choices": [{"delta": {"content": content}}]}, {"choices": [], "usage": {}}]
        return httpx.Response(200, text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n")
    return httpx.Response(200, json={"choices": [{"message": {"content": content, **message}}]})


def serve(monkeypatch, reply="47815"):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "/models/Qwen3.8-27B-UD-IQ4_XS.gguf"}]})
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return answer(body, reply)
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


def test_a_server_that_cannot_chat_is_not_adopted(app_db, monkeypatch):
    serve(monkeypatch, reply="")
    models.load()
    before = models.active("assistant")
    with pytest.raises(RuntimeError, match="empty answer"):
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
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body.get("model"))
        if body.get("model") != "qwen3-vl-4b":
            return httpx.Response(500, json={"error": {"message": "model is not a chat model"}})
        return answer(body)
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
    assert len(seen) > 2 and set(seen) == {"qwen3-vl-4b"}


def test_thinking_is_turned_off_with_reasoning_effort_unless_the_server_rejects_it(app_db, monkeypatch):
    for strict in (False, True):                    # LocalAI accepts "none"; a strict server only knows low/medium/high
        bodies = []

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "qwen3.5-9b"}]})
            if request.url.path != "/v1/chat/completions":
                return httpx.Response(404)
            body = json.loads(request.content)
            bodies.append(body)
            if strict and "reasoning_effort" in body:
                return httpx.Response(400, json={"error": {"message": "reasoning_effort: Input should be 'low', 'medium' or 'high'"}})
            return answer(body)
        monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        events = asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
        assert events[-1]["type"] == "switched"
        assert models.active("assistant").think_off == ("chat_template_kwargs" if strict else "reasoning_effort")
        assert bodies[-1].get("reasoning_effort") == (None if strict else "none")


# --- engine profiles ---

def test_bundled_llama_requests_are_unchanged():
    body = {"messages": [], "repetition_penalty": 1.0, "chat_template_kwargs": {"enable_thinking": False}}
    assert engines.prepare(models.LLAMA_CATALOG[0], body) == (body, {})


def server(**kw):
    return models.Spec("assistant", "http://127.0.0.1:8080/v1", "", provider="server", **{"engine": "generic", **kw})


@pytest.mark.parametrize("mode,expected", [
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("reasoning_effort", {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}),
    ("enable_thinking", {"enable_thinking": False}),
])
def test_thinking_off_is_written_the_way_the_server_was_tested_with(mode, expected):
    body, headers = engines.prepare(server(think_off=mode, model="m", key="k"),
                                    {"messages": [], "chat_template_kwargs": {"enable_thinking": False}})
    assert body == {"messages": [], **expected, "model": "m"} and headers == {"Authorization": "Bearer k"}


def test_thinking_switches_are_only_added_when_thinking_is_turned_off():
    body, _ = engines.prepare(server(think_off="reasoning_effort"), {"messages": []})
    assert body == {"messages": []}


def test_fields_the_engine_refuses_are_dropped(monkeypatch):
    monkeypatch.setitem(engines.ENGINES, "strict", engines.Engine("strict", "Strict", ("chat_template_kwargs",),
                                                                  drop=("repetition_penalty",)))
    body, _ = engines.prepare(server(engine="strict"), {"messages": [], "repetition_penalty": 1.0})
    assert body == {"messages": []}


@pytest.mark.parametrize("old,think_off", [({"effort_none": True}, "reasoning_effort"),
                                           ({"effort_none": False}, "chat_template_kwargs"),
                                           ({}, "chat_template_kwargs")])
def test_servers_saved_by_older_versions_keep_their_thinking_switch(app_db, old, think_off):
    saved = {"role": "assistant", "repo": "http://127.0.0.1:8080/v1", "file": "", "provider": "server", **old}
    with dbmod.db() as conn:
        dbmod.set_setting(conn, "model.assistant", saved)
    models.load()
    spec = models.active("assistant")
    assert (spec.engine, spec.think_off, spec.caps) == ("generic", think_off, engines.CAPS)


# --- capability probe ---

def probing(monkeypatch, *, image=True, schema=True, tools=True, thinks=False, stream=True, n_ctx=None, delay=0.0):
    """A server with the given capabilities. One it lacks is refused with an error or simply not used, as real ones do."""
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m", "max_model_len": 32768}]})
        if request.url.path == "/props":
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": n_ctx}}) if n_ctx else httpx.Response(404)
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        thinking = {"reasoning_content": "Let me think…"} if thinks else {}
        if body.get("stream"):
            return answer(body, "OK") if stream else httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        if isinstance(content, list):
            await asyncio.sleep(delay)
            return answer(body, "47815", **thinking) if image else httpx.Response(500, json={"error": {"message": "image input is not supported"}})
        if "response_format" in body:
            return answer(body, '{"answer": 5}') if schema else httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        if "tools" in body:
            calls = [{"id": "1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Rome"}'}}]
            return answer(body, "", tool_calls=calls) if tools else answer(body, "I can't check the weather.")
        return answer(body, "OK", **thinking)
    monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))


def test_the_probe_reports_each_capability_and_stores_what_the_server_can_do(app_db, monkeypatch):
    probing(monkeypatch, n_ctx=65536)
    events = asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    assert [e["cap"] for e in events if e.get("step") == "probe"] == list(engines.CAPS[:2]) + ["vision", "json_schema", "tools"]
    spec = models.active("assistant")
    assert spec.caps == engines.CAPS and spec.context == 65536
    models._active.clear()
    models.load()                                   # survives a restart
    assert models.active("assistant").caps == engines.CAPS


def test_a_server_missing_optional_capabilities_is_still_used(app_db, monkeypatch):
    probing(monkeypatch, image=False, schema=False, tools=False)
    events = asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    spec = models.active("assistant")
    assert events[-1]["type"] == "switched" and spec.caps == ("chat", "stream", "think_off")
    assert spec.can("chat") and not spec.can("vision") and not spec.can("tools")
    assert spec.context == 32768                    # no /props: vLLM's max_model_len


def test_a_server_that_keeps_thinking_is_used_without_the_think_off_capability(app_db, monkeypatch):
    probing(monkeypatch, thinks=True)
    asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    spec = models.active("assistant")
    assert spec.think_off == "reasoning_effort" and "think_off" not in spec.caps and "chat" in spec.caps


def test_a_server_that_keeps_thinking_gets_room_to_answer_the_probes(app_db, monkeypatch):
    """A reasoning model fills a small budget with reasoning; the probes give it enough tokens to answer, and a
    stream that only carries reasoning still proves the server streams."""
    budgets = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        body = json.loads(request.content)
        budgets.append(body["max_tokens"])
        if body.get("stream"):
            chunk = {"choices": [{"delta": {"reasoning_content": "Let me think…"}}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
        if body["max_tokens"] < models.THINKING_PROBE_TOKENS and "response_format" in body:
            return answer(body, "", reasoning_content="Let me think…" * 100)
        if "response_format" in body:
            return answer(body, '{"answer": 5}', reasoning_content="Let me think…")
        return answer(body, "OK", reasoning_content="Let me think…")
    monkeypatch.setattr(models, "_http", httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    events = asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    assert events[-1]["type"] == "switched"
    assert "json_schema" in models.active("assistant").caps
    assert set(budgets[3:]) == {models.THINKING_PROBE_TOKENS}   # after the three think-off attempts


def test_a_server_that_cannot_stream_is_not_adopted(app_db, monkeypatch):
    probing(monkeypatch, stream=False)
    models.load()
    before = models.active("assistant")
    with pytest.raises(RuntimeError, match="did not stream"):
        asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    assert models.active("assistant") == before


def test_optional_probes_that_run_out_of_time_count_as_missing(app_db, monkeypatch):
    probing(monkeypatch, delay=5)
    monkeypatch.setattr(models, "PROBE_BUDGET", 0.1)
    asyncio.run(run_switch("assistant", "http://127.0.0.1:8080"))
    assert "vision" not in models.active("assistant").caps


def test_bundled_models_have_every_capability():
    assert all(models.LLAMA_CATALOG[0].can(cap) for cap in engines.CAPS)
