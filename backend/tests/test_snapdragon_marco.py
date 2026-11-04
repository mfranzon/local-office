"""Regression coverage for maintainer review: lifecycle, integrity and model contracts."""
import asyncio
from collections import deque
from concurrent.futures import Future
from dataclasses import replace
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from app import llm, models, npu, npu_worker as worker, provision, runtime


class Stream:
    def __init__(self, text, stop="eos"):
        self.output = SimpleNamespace(text=text, profile=SimpleNamespace(stop_reason=stop))
    def __iter__(self):
        return iter([self.output.text])


@pytest.fixture
def model(monkeypatch):
    fake = SimpleNamespace(reset=Mock(), supports_thinking=False,
                           tokenizer=SimpleNamespace(apply_chat_template=Mock(return_value="prompt")),
                           generate=Mock(return_value=Stream("Done")))
    monkeypatch.setattr(worker, "_model", fake)
    monkeypatch.setattr(worker, "_tokenizer", None)
    monkeypatch.setattr(worker, "_spec", {"role": "assistant", "context": 4096})
    monkeypatch.setattr(worker, "emit", Mock())
    return fake


def image_part(side=32):
    image = io.BytesIO()
    Image.new("RGB", (side, side)).save(image, "PNG")
    return llm.image_part(image.getvalue())


def test_role_switches_keep_embedder_and_current_generator(monkeypatch, tmp_path):
    spawned = []
    def spawn(*a, **k):
        proc = Mock()
        proc.poll.return_value = None
        def terminate():
            proc.poll.return_value = 0
        proc.terminate.side_effect = terminate
        spawned.append(proc)
        return proc
    monkeypatch.setattr(npu, "_workers", {})
    monkeypatch.setattr(npu, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(npu.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "_kill_with_app", lambda proc: None)
    embed = models.Spec("embed", "test", "embed")
    assistant = models.Spec("assistant", "test", "assistant")
    ocr = models.Spec("ocr", "test", "ocr")
    e = npu._start(embed)
    a = npu._start(assistant)
    assert npu._start(embed) is e and npu._start(assistant) is a
    o = npu._start(ocr)
    assert a.proc.terminate.called and not e.proc.terminate.called
    assert npu._start(embed) is e and npu._start(ocr) is o
    assert len(spawned) == 3
    npu.shutdown()


@pytest.fixture
def protocol(monkeypatch):
    lines = deque()
    proc = SimpleNamespace(stdin=SimpleNamespace(write=lambda data: None, flush=lambda: None),
                           stdout=SimpleNamespace(readline=lambda: lines.popleft()), poll=lambda: None)
    instance = npu.Worker(proc, "assistant", "test")
    monkeypatch.setattr(npu, "_lock", asyncio.Lock())
    monkeypatch.setattr(npu, "have", lambda spec: True)
    monkeypatch.setattr(npu, "_start", lambda spec: instance)
    stop = Mock()
    monkeypatch.setattr(npu, "shutdown", stop)
    return lines, proc, instance, stop


def test_recoverable_error_is_drained_and_reuses_worker_with_glued_stdout(protocol):
    lines, _, _, stop = protocol
    lines.extend(['native fragmentLO_EVENT:{"type":"error","message":"context too long","fatal":false}\n',
                  'LO_EVENT:{"type":"done"}\n',
                  'LO_EVENT:{"type":"message","message":{"content":"OK"}}\n',
                  'LO_EVENT:{"type":"done"}\n'])
    spec = models.Spec("assistant", "test", "model")
    async def run():
        with pytest.raises(RuntimeError, match="context too long"):
            await npu.complete(spec, [])
        assert (await npu.complete(spec, []))["content"] == "OK"
    asyncio.run(run())
    assert not stop.called and not lines


def test_progress_resets_idle_deadline(protocol, monkeypatch):
    lines, proc, _, stop = protocol
    lines.extend(['LO_EVENT:{"type":"progress"}\n'] * 10 + [
        'LO_EVENT:{"type":"message","message":{"content":"OK"}}\n', 'LO_EVENT:{"type":"done"}\n'])
    def readline():
        time.sleep(.05)
        return lines.popleft()
    proc.stdout.readline = readline
    # 10x headroom per event (slow CI runners), while the whole request (~0.6 s) still exceeds the deadline.
    monkeypatch.setattr(npu, "IDLE_TIMEOUT", .5)
    async def run():
        return await npu.complete(models.Spec("assistant", "test", "model"), [])
    assert asyncio.run(run())["content"] == "OK"
    assert not stop.called  # Total request exceeds the deadline but each event is timely.


def test_idle_timeout_stops_only_selected_slot(protocol, monkeypatch):
    _, proc, _, stop = protocol
    def readline():
        time.sleep(.06)
        return ""
    proc.stdout.readline = readline
    monkeypatch.setattr(npu, "IDLE_TIMEOUT", .01)
    async def run():
        with pytest.raises(TimeoutError):
            await npu.complete(models.Spec("assistant", "test", "model"), [])
    asyncio.run(run())
    stop.assert_called_once_with("generation")


def test_reap_waits_outside_event_loop(monkeypatch):
    monkeypatch.setattr(npu, "_workers", {"embed": SimpleNamespace(last_used=float("-inf"))})
    monkeypatch.setattr(npu, "_lock", asyncio.Lock())
    monkeypatch.setattr(npu, "shutdown", lambda slot: time.sleep(.09))
    ticks = []
    async def run():
        async def ticker():
            for _ in range(6):
                await asyncio.sleep(.01)
                ticks.append(True)
        task = asyncio.create_task(ticker())
        await npu.reap()
        assert len(ticks) >= 4
        await task
    asyncio.run(run())


def test_cancelled_startup_finishes_before_lock_release(protocol, monkeypatch):
    _, _, instance, stop = protocol
    completed = []
    def start(spec):
        time.sleep(.06)
        completed.append(True)
        return instance
    monkeypatch.setattr(npu, "_start", start)
    async def run():
        task = asyncio.create_task(npu.complete(models.Spec("assistant", "test", "model"), []))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed and not npu._lock.locked()
    asyncio.run(run())
    stop.assert_called_once_with("generation")


def test_context_fitting_never_changes_system_or_latest_user(model, monkeypatch):
    system = "TOOL_SCHEMA_AND_PROTOCOL:" + "s" * 2200
    messages = [{"role": "system", "content": system},
                {"role": "tool", "content": "d" * 18000}, {"role": "user", "content": "Summarize accurately"}]
    model.tokenizer.apply_chat_template.side_effect = lambda msgs, **k: json.dumps(msgs)
    monkeypatch.setattr(worker, "_tokenizer", SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[0] * (len(text)//4))))
    prompt, budget = worker.fit_prompt(messages, None, 2500)
    rendered = json.loads(prompt)
    assert rendered[0]["content"] == system and rendered[-1] == messages[-1]
    assert "[excerpt shortened]" in rendered[1]["content"]
    assert messages[1]["content"] == "d" * 18000 and budget > 0


def test_oversized_system_raises_instead_of_cutting_tools(model, monkeypatch):
    monkeypatch.setattr(worker, "_spec", {"context": 512})
    model.tokenizer.apply_chat_template.return_value = "x" * 1000
    with pytest.raises(worker.RequestError, match="exceeds"):
        worker.fit_prompt([{"role": "system", "content": "protected schema"}, {"role": "user", "content": "hi"}], None, 256)


def test_assistant_honors_2500_output_tokens_and_signals_truncation(model):
    worker.chat({"messages": [{"role": "user", "content": "Explain"}], "params": {"max_tokens": 2500}})
    assert model.generate.call_args.kwargs["max_new_tokens"] == 2500
    model.generate.return_value = Stream("partial", "length")
    with pytest.raises(worker.RequestError, match="reached its token/context limit"):
        worker.chat({"messages": [{"role": "user", "content": "Explain"}], "params": {"max_tokens": 2500}})


def test_vision_reserves_image_tokens_and_ocr_penalty_is_neutral(model, monkeypatch):
    monkeypatch.setattr(worker, "_spec", {"role": "vision", "context": 2048})
    worker.chat({"messages": [{"role": "user", "content": [image_part(840)]}], "params": {"max_tokens": 1536}})
    assert model.generate.call_args.kwargs["max_new_tokens"] < 1536
    monkeypatch.setattr(worker, "_spec", {"role": "ocr", "context": 4096})
    worker.chat({"messages": [{"role": "user", "content": [image_part()]}]})
    assert model.generate.call_args.kwargs["repetition_penalty"] == 1.0


@pytest.mark.parametrize("bad", ['<tool_call>{bad json}</tool_call>', '<tool_call>{"name":"delete_file","arguments":{}}</tool_call>'])
def test_bad_tool_calls_are_repaired_without_aborting(model, bad):
    model.generate.side_effect = [Stream(bad), Stream('{"name":"read_document","arguments":{"doc_id":"d1"}}')]
    tools = [{"type": "function", "function": {"name": "read_document"}}]
    worker.chat({"messages": [{"role": "user", "content": "Read the document"}], "params": {"tools": tools}})
    assert model.generate.call_count == 2
    retry = model.tokenizer.apply_chat_template.call_args.args[0]
    assert [m["role"] for m in retry[-2:]] == ["assistant", "user"]
    assert worker.emit.call_args.kwargs["message"]["tool_calls"][0]["function"]["name"] == "read_document"


def test_name_field_is_not_mistaken_for_tool_and_final_step_can_repair_tags(model):
    assert worker.parse_message('{"name":"Acme"}', [{"function": {"name": "read_document"}}])["content"] == '{"name":"Acme"}'
    model.generate.side_effect = [Stream('<tool_call>{"name":"read_document"}</tool_call>'), Stream("Final answer")]
    worker.chat({"messages": [{"role": "user", "content": "Finish"}]})
    assert worker.emit.call_args.kwargs["message"]["content"] == "Final answer"


def test_schema_retry_supports_multimodal_history_and_refits(model):
    pytest.importorskip("jsonschema")
    model.generate.side_effect = [Stream('{bad json}'), Stream('{"value":"47815"}')]
    schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}}
    worker.chat({"messages": [{"role": "user", "content": [image_part()]}],
                 "params": {"response_format": {"json_schema": {"schema": schema}}}})
    retry = model.tokenizer.apply_chat_template.call_args.args[0]
    assert isinstance(retry[1]["content"], list)
    assert [m["role"] for m in retry[-2:]] == ["assistant", "user"]
    assert json.loads(worker.emit.call_args.kwargs["message"]["content"])["value"] == "47815"


def test_request_validation_error_is_nonfatal_and_next_request_runs(model, monkeypatch):
    calls = []
    def chat(request):
        calls.append(request)
        if len(calls) == 1:
            raise worker.RequestError("Conversation exceeds context")
    monkeypatch.setattr(worker, "chat", chat)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO('{"op":"chat"}\n{"op":"chat"}\n'))
    worker.main()
    events = worker.emit.call_args_list
    assert events[0].kwargs["fatal"] is False
    assert [call.args[0] for call in events] == ["error", "done", "done"]
    assert len(calls) == 2 and model.reset.called


def test_archive_pin_rejects_changed_bytes_and_existing_runtime_without_marker(tmp_path, monkeypatch):
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"tampered")
    monkeypatch.setattr(provision, "REPO", tmp_path)
    installed = tmp_path / "build/geniex/lib/geniex.dll"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"previous runtime")
    monkeypatch.setattr(provision, "download", lambda *a: archive)
    monkeypatch.setattr(provision, "extract", Mock(side_effect=AssertionError("must not extract unverified bytes")))
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        provision.install_runtime()
    assert not provision.extract.called
    assert not archive.exists()  # A bad cached archive is discarded so a retry downloads again.


def test_install_marker_tracks_the_pinned_digest(tmp_path, monkeypatch):
    assert not provision.installed(tmp_path, "nomic")
    provision.mark_installed(tmp_path, "nomic")
    assert provision.installed(tmp_path, "nomic")
    monkeypatch.setitem(provision.ASSETS, "nomic", provision.ASSETS["nomic"]._replace(sha256="0" * 64))
    assert not provision.installed(tmp_path, "nomic")  # Bumping a pin invalidates older installs.


def test_nomic_archive_is_verified_before_extraction(tmp_path, monkeypatch):
    archive = tmp_path / "nomic.zip"
    archive.write_bytes(b"tampered model archive")
    monkeypatch.setattr(npu, "have", lambda spec: False)
    monkeypatch.setattr(npu, "EMBED_DIR", tmp_path / "installed")
    monkeypatch.setattr(provision, "download", lambda *args: archive)
    monkeypatch.setattr(provision, "extract", Mock(side_effect=AssertionError("must verify before extraction")))
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        provision.install_model(models.Spec("embed", "test", "", provider="aihub"))
    assert not provision.extract.called


def test_failed_download_cancels_queued_chunks(tmp_path, monkeypatch):
    pending = []
    class Pool:
        def __init__(self, **kwargs):
            pass
        def submit(self, fn, index):
            future = Future()
            if index == 0:
                future.set_exception(RuntimeError("chunk failed"))
            else:
                pending.append(future)
            return future
        def shutdown(self, wait, cancel_futures=False):
            assert cancel_futures
            for future in pending:
                future.cancel()
    response = io.BytesIO()
    response.headers = {"Content-Length": str(4 * 1024 * 1024), "ETag": "test"}
    monkeypatch.setattr(provision.urllib.request, "urlopen", lambda *a, **k: response)
    monkeypatch.setattr(provision, "ThreadPoolExecutor", Pool)
    with pytest.raises(RuntimeError, match="chunk failed"):
        provision.download("https://example.com/model", tmp_path / "model.zip")
    assert len(pending) == 3 and all(f.cancelled() for f in pending)
