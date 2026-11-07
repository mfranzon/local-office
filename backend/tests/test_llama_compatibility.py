"""Regressions for the existing Windows llama-server backend.

HTTP is mocked; these checks do not certify x64/CUDA hardware execution.
"""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from app import llm, models


@pytest.mark.skipif(sys.platform != "win32", reason="Windows architecture selection")
@pytest.mark.parametrize("machine,backend,npu", [
    ("AMD64", None, False),
    ("AMD64", "llama", False),
    ("ARM64", None, False),
    ("ARM64", "llama", False),
    ("ARM64", "snapdragon", True),
])
def test_windows_backend_selection_in_fresh_process(machine, backend, npu):
    env = os.environ.copy()
    env.pop("LOCAL_OFFICE_BACKEND", None)
    env.pop("EMBED_DIM", None)
    if backend is not None:
        env["LOCAL_OFFICE_BACKEND"] = backend
    code = f"""
import platform
platform.machine = lambda: {machine!r}
import json, sys, types
# This host lacks the x64 sqlite-vec wheel; only platform selection is under test.
sys.modules['sqlite_vec'] = types.ModuleType('sqlite_vec')
from app import config, models
print(json.dumps({{
    'npu': config.SNAPDRAGON, 'roles': config.ROLES, 'dim': config.EMBED_DIM,
    'providers': sorted(set(s.provider for s in models.CATALOG)),
    'native_imported': any(n in sys.modules for n in ('geniex', 'onnxruntime', 'app.npu_worker')),
}}))
"""
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["npu"] is npu
    assert config["roles"] == (["assistant", "ocr", "embed", "vision"] if npu else ["assistant", "ocr", "embed"])
    assert config["dim"] == (512 if npu else 1024)
    assert config["providers"] == (["aihub", "geniex-gguf", "geniex-qairt"] if npu else ["llama"])
    assert not config["native_imported"]


def test_existing_saved_model_without_provider_still_loads_as_llama():
    saved = asdict(models.LLAMA_CATALOG[0])
    saved.pop("provider")
    spec = models.Spec(**saved)
    assert spec.provider == "llama"
    assert spec.files == [saved["file"], saved["mmproj"]]


def test_mac_configuration_keeps_original_roles_and_no_native_npu_imports():
    env = os.environ.copy()
    env.pop("LOCAL_OFFICE_BACKEND", None)
    env.pop("EMBED_DIM", None)
    code = """
import sys, platform, subprocess, types, urllib.request
from unittest.mock import patch
# Supply the Mac-only native dependency when simulating Mac imports on Windows.
sys.modules['sqlite_vec'] = types.ModuleType('sqlite_vec')
with patch.object(sys, 'platform', 'darwin'), patch.object(platform, 'machine', return_value='arm64'), patch.object(subprocess, 'run') as run:
    run.return_value.stdout = '25769803776'
    from app import config, models
    assert config.MAC and not config.SNAPDRAGON
    assert config.ROLES == ('assistant', 'ocr', 'embed')
    assert config.EMBED_DIM == 1024
    assert models.CATALOG is models.LLAMA_CATALOG
    assert models.recommended('assistant', 24).name == 'Qwen3.5 9B'
    assert not any(n in sys.modules for n in ('geniex', 'onnxruntime', 'app.npu_worker'))
"""
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_vector_query_preserves_existing_scoped_candidate_count(monkeypatch):
    from app import db
    from unittest.mock import Mock
    monkeypatch.setattr(db, "sqlite_vec", object())
    conn = Mock()
    conn.execute.return_value.fetchall.return_value = [(1,)]
    assert db.nearest(conn, [1., 0.], 40, ["doc1"]) == [(1,)]
    sql, params = conn.execute.call_args.args
    assert "embedding MATCH ? AND k = ?" in sql
    assert params == (db.vec([1., 0.]), 160)


def test_legacy_wiki_windowing_is_preserved(monkeypatch):
    from app import wiki
    monkeypatch.setattr(wiki, "SNAPDRAGON", False)
    monkeypatch.setattr(wiki, "WINDOW_CHARS", 9000)
    pages = [{"n": 1, "text": "a" * 10000}, {"n": 2, "text": "next page"}]
    assert wiki._windows(pages) == ["=== Page 1 ===\n" + "a" * 9000 + "\n\n", "=== Page 2 ===\nnext page\n\n"]


@pytest.mark.parametrize("mode", ["vision", "ocr", "table"])
def test_mac_image_actions_keep_original_assistant_stream(monkeypatch, mode):
    import io
    from fastapi.testclient import TestClient
    from PIL import Image
    from app import main, rag

    monkeypatch.setattr(main, "SNAPDRAGON", False)
    seen = []

    async def stream(messages, **kwargs):
        seen.append(messages)
        yield "Image answer"

    async def unexpected_ocr(*args, **kwargs):
        pytest.fail("The Mac image route must keep using the assistant")

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "chat", unexpected_ocr)
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, "PNG")
    client = TestClient(main.app, base_url="http://127.0.0.1")  # No lifespan: this test does not load models.
    response = client.post("/api/vision", files={"image": ("page.png", image.getvalue(), "image/png")},
        data={"mode": mode, "messages": json.dumps([{"role": "user", "content": "Read this image"}])})
    assert response.status_code == 200 and "Image answer" in response.text
    assert seen[0][0] == {"role": "system", "content": rag.SYSTEM_PLAIN}
    assert seen[0][1]["content"][0]["type"] == "image_url"
    assert seen[0][1]["content"][1]["text"] == "Read this image"


def test_llama_http_completion_streaming_images_and_embeddings(monkeypatch):
    monkeypatch.setattr(llm, "SNAPDRAGON", False)
    embed_spec = next(s for s in models.LLAMA_CATALOG if s.role == "embed")
    monkeypatch.setattr(llm, "active", lambda role: embed_spec)
    roles = []
    requests = []

    async def url(role):
        roles.append(role)
        return "http://127.0.0.1:12345/v1"

    def respond(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [
                {"index": 1, "embedding": [0., 1.]},
                {"index": 0, "embedding": [1., 0.]},
            ]})
        if body.get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "Result", "tool_calls": [{"id": "call1"}]
        }}]})

    monkeypatch.setattr(llm, "url", url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(llm, "_http", client)
            messages = [{"role": "user", "content": "Read the document"}]
            result = await llm.complete("ocr", messages, max_tokens=128)
            assert result["content"] == "Result" and result["tool_calls"][0]["id"] == "call1"
            image = [{"role": "user", "content": [llm.image_part(b"local image")]}]
            assert [t async for t in llm.chat_stream(image)] == ["Hello"]
            assert await llm.embed(["first", "second"], query=True) == [[1., 0.], [0., 1.]]

    asyncio.run(run())
    assert roles == ["ocr", "assistant", "embed"]
    assert requests[0][1]["max_tokens"] == 128
    assert requests[1][1]["messages"][0]["content"][0]["type"] == "image_url"
    assert requests[2][1]["input"] == [embed_spec.query_prefix + t for t in ("first", "second")]


def test_llama_model_still_uses_runtime_supervisor(monkeypatch):
    spec = models.LLAMA_CATALOG[0]
    monkeypatch.setattr(models, "active", lambda role: spec)
    monkeypatch.setattr(models.runtime, "have", lambda repo, files: True)
    calls = []

    async def ensure(role, key, args):
        calls.append((role, key, args))
        return "http://127.0.0.1:12345/v1"

    monkeypatch.setattr(models.runtime, "ensure", ensure)
    assert asyncio.run(models.url("assistant")) == "http://127.0.0.1:12345/v1"
    assert calls == [("assistant", spec.ref, spec.args)]
    assert "--mmproj" in calls[0][2]
    assert "--jinja" in calls[0][2]
