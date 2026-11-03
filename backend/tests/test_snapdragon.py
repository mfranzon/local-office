import asyncio
import json

import pytest

from app import db as storage, llm, models, npu, wiki
from app.npu_worker import parse_message


def test_qairt_state_is_reset_before_rendering_tools(monkeypatch):
    from app import npu_worker
    from types import SimpleNamespace
    state = {"reset": False}
    def reset():
        state["reset"] = True
    def template(messages, **kwargs):
        assert state["reset"], "QAIRT drops tools when its old conversation state is retained"
        assert kwargs["tools"][0]["function"]["name"] == "read_document"
        return "prompt"
    fake = SimpleNamespace(reset=reset, supports_thinking=False,
        tokenizer=SimpleNamespace(apply_chat_template=template),
        generate=lambda *a, **k: SimpleNamespace(text="Done", profile=SimpleNamespace(stop_reason="eos")))
    monkeypatch.setattr(npu_worker, "_model", fake)
    monkeypatch.setattr(npu_worker, "_tokenizer", None)
    monkeypatch.setattr(npu_worker, "_spec", {"context": 4096})
    monkeypatch.setattr(npu_worker, "emit", lambda *a, **k: None)
    npu_worker.chat({"messages": [{"role": "user", "content": "Read my document"}],
                     "params": {"tools": [{"function": {"name": "read_document"}}]}})


def test_tool_calls_preserve_arguments_and_reject_unoffered_functions():
    tools = [{"type": "function", "function": {"name": "create_file"}}]
    result = parse_message('<tool_call>{"name":"create_file","arguments":{"filename":"Invoices","format":"xlsx"}}</tool_call>', tools)
    assert json.loads(result["tool_calls"][0]["function"]["arguments"])["format"] == "xlsx"
    with pytest.raises(ValueError, match="unknown tool"):
        parse_message('<tool_call>{"name":"delete_file","arguments":{}}</tool_call>', tools)


def test_arm_vector_search_ranks_cosine_and_filters_document_scope(tmp_path, monkeypatch):
    pytest.importorskip("numpy", reason="The ARM64 fallback dependency is not required by the Mac app")
    monkeypatch.setattr(storage, "sqlite_vec", None)
    monkeypatch.setattr(storage, "VEC_TABLE", "CREATE TABLE IF NOT EXISTS vec_chunks (rowid INTEGER PRIMARY KEY, embedding BLOB NOT NULL)")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    storage.init()
    with storage.db() as conn:
        for name in ("a", "b"):
            conn.execute("INSERT INTO documents(id,name,ext,size) VALUES (?,?,'.txt',1)", (name, name))
        for doc, vector in [("a", [1., 0.]), ("b", [.8, .2]), ("b", [0., 1.])]:
            row = conn.execute("INSERT INTO chunks(doc_id,page,text) VALUES (?,1,'test')", (doc,)).lastrowid
            conn.execute("INSERT INTO vec_chunks(rowid,embedding) VALUES (?,?)", (row, storage.vec(vector)))
        assert storage.nearest(conn, [1., 0.], 3) == [(1,), (2,), (3,)]
        assert storage.nearest(conn, [1., 0.], 3, ["b"]) == [(2,), (3,)]


def test_image_requests_use_vision_and_plain_requests_use_assistant(monkeypatch):
    monkeypatch.setattr(llm, "SNAPDRAGON", True)
    monkeypatch.setattr(llm, "active", lambda role: role)
    roles = []
    async def events(spec, request):
        roles.append(spec)
        yield {"type": "token", "text": "answer"}
    monkeypatch.setattr(npu, "events", events)
    async def run():
        for content in ("Hello", [llm.image_part(b"test")]):
            assert [v async for v in llm.chat_stream([{"role": "user", "content": content}])] == ["answer"]
    asyncio.run(run())
    assert roles == ["assistant", "vision"]


def test_wiki_windows_retain_end_of_long_pages(monkeypatch):
    monkeypatch.setattr(wiki, "SNAPDRAGON", True)
    monkeypatch.setattr(wiki, "WINDOW_CHARS", 3500)
    windows = wiki._windows([{"n": 7, "text": "a" * 6000 + "The final payment is due in December."}])
    assert len(windows) == 2
    assert "final payment" in windows[-1]
    assert all("=== Page 7 ===" in window for window in windows)
