"""Failure-path regressions from the Snapdragon PR review; no native SDK needed."""
import asyncio
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from app import agent, llm, npu, npu_worker, provision, skills, tools


@pytest.mark.parametrize("stop", ["length", "context_length", "eos"])
def test_ocr_uses_requested_budget_and_rejects_partial_output(monkeypatch, stop):
    emitted = []
    budgets = []

    def generate(prompt, **kwargs):
        budgets.append(kwargs["max_new_tokens"])
        class Stream:
            output = SimpleNamespace(text="partial page", profile=SimpleNamespace(stop_reason=stop))
            def __iter__(self):
                return iter(["partial page"])
        return Stream()

    fake = SimpleNamespace(reset=lambda: None, supports_thinking=False,
        tokenizer=SimpleNamespace(apply_chat_template=lambda *a, **k: "prompt"), generate=generate)
    monkeypatch.setattr(npu_worker, "_model", fake)
    monkeypatch.setattr(npu_worker, "_spec", {"role": "ocr", "context": 4096})
    monkeypatch.setattr(npu_worker, "_tokenizer", None)
    monkeypatch.setattr(npu_worker, "emit", lambda *a, **k: emitted.append((a, k)))
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (32, 32)).save(image, "PNG")
    request = {"messages": [{"role": "user", "content": [llm.image_part(image.getvalue())]}],
               "params": {"max_tokens": 4096}}
    if stop == "eos":
        npu_worker.chat(request)
        assert emitted[-1][1]["message"]["content"] == "partial page"
    else:
        with pytest.raises(npu_worker.RequestError, match="OCR output was truncated"):
            npu_worker.chat(request)
        assert not any(a[0] == "message" for a, _ in emitted)
    assert budgets[0] > 1536


def test_embeddings_drain_done_before_reusing_worker(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Spec:
        role: str = "embed"
        name: str = "test"

    lines = []
    starts = []
    proc = SimpleNamespace(poll=lambda: None)

    def write(request):
        lines.extend(['LO_EVENT:{"type":"embeddings","vectors":[[1.0]]}\n',
                      'LO_EVENT:{"type":"done"}\n'])

    proc.stdin = SimpleNamespace(write=write, flush=lambda: None)
    proc.stdout = SimpleNamespace(readline=lambda: lines.pop(0))
    worker = npu.Worker(proc, "embed", "test")
    monkeypatch.setattr(npu, "_lock", asyncio.Lock())
    monkeypatch.setattr(npu, "have", lambda spec: True)
    def start(spec):
        starts.append(proc)
        return worker
    monkeypatch.setattr(npu, "_start", start)
    monkeypatch.setattr(npu, "shutdown", lambda: pytest.fail("A completed embedding request must not terminate its worker"))

    async def run():
        for _ in range(2):
            assert await npu.embed(Spec(), ["document"]) == [[1.0]]
            assert not lines  # Includes the terminal done marker, before the next request.
            assert not npu._lock.locked()

    asyncio.run(run())
    assert starts == [proc, proc]


@pytest.mark.parametrize("query", ["Explain this file format", "Write a word of thanks", "Explain Excel formulas", "Summarize these instructions"])
def test_source_free_skills_do_not_trigger_file_or_source_guards(tmp_path, monkeypatch, query):
    seen = []
    async def complete(role, messages, **kwargs):
        seen.append(messages)
        return {"content": "Here is the explanation."}
    monkeypatch.setattr(llm, "complete", complete)
    skill = skills.Skill("test", "Test", "Answer general office questions.", tmp_path, builtin=False)
    async def run():
        return [e async for e in agent.run(skill, [{"role": "user", "content": query}])]
    result = asyncio.run(run())
    assert len(seen) == 1
    assert any(e.get("text") == "Here is the explanation." for e in result)


@pytest.mark.parametrize("checkpoint", ["", '{"done":', "[]", '{"size":1,"done":null}'])
def test_download_recovers_from_bad_checkpoint(tmp_path, monkeypatch, checkpoint):
    dest = tmp_path / "model.zip"
    part = dest.with_suffix(".zip.part")
    state = dest.with_suffix(".zip.progress.json")
    data = b"a" * (1024 * 1024) + b"tail"
    part.write_bytes(b"x" * len(data))
    state.write_text(checkpoint)
    ranges = []

    def urlopen(request, **kwargs):
        if request.get_method() == "HEAD":
            response = io.BytesIO()
            response.headers = {"Content-Length": str(len(data)), "ETag": "version1"}
            return response
        span = request.get_header("Range")
        ranges.append(span)
        start, end = map(int, span.removeprefix("bytes=").split("-"))
        response = io.BytesIO(data[start:end + 1])
        response.status = 206
        return response

    monkeypatch.setattr(provision.urllib.request, "urlopen", urlopen)
    assert provision.download("https://example.com/model.zip", dest) == dest
    assert dest.read_bytes() == data
    assert len(ranges) == 2
    assert not state.exists() and not state.with_suffix(state.suffix + ".tmp").exists()


@pytest.mark.parametrize("filename,valid", [("report.md", False), ("report.docx", False), ("report.xlsx", True)])
def test_workbook_smoke_check_rejects_other_file_types(monkeypatch, filename, valid):
    import httpx
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["LO-47815", 3000])
    buffer = io.BytesIO()
    wb.save(buffer)
    event = {"type": "file", "name": filename, "url": "/export"}

    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
        return httpx.Response(200, content=buffer.getvalue())

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    script = Path(__file__).resolve().parents[2] / "scripts/check_skill.py"
    if valid:
        runpy.run_path(str(script))
    else:
        with pytest.raises(AssertionError, match="No workbook"):
            runpy.run_path(str(script))
