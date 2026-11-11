"""Thin async client for the per-role llama-server instances. models.url() starts a role's server on
first use, so callers never need to know whether a model is loaded."""
import base64
import json
from collections.abc import AsyncIterator

import httpx

from . import runtime
from .config import ROLES, SNAPDRAGON
from .models import active, url

_http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5))
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


async def chat(role: str, messages: list[dict], **params) -> str:
    return (await complete(role, messages, **params))["content"] or ""


async def complete(role: str, messages: list[dict], **params) -> dict:
    """One non-streamed reply as a message dict, including any tool_calls."""
    if SNAPDRAGON:
        from . import npu
        return await npu.complete(active(role), messages, **params)
    r = await _http.post(f"{await url(role)}/chat/completions", json={"messages": messages, "temperature": 0, **params})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


async def complete_stream(role: str, messages: list[dict], **params) -> AsyncIterator[dict]:
    """complete(), but the reply is yielded while it is being written: the same dict, growing, and whole at the
    last yield. A tool call's arguments arrive as unfinished JSON text."""
    if SNAPDRAGON:
        yield await complete(role, messages, **params)
        return
    msg = {"content": "", "tool_calls": []}
    body = {"messages": messages, "temperature": 0, "stream": True, **params}
    async with _http.stream("POST", f"{await url(role)}/chat/completions", json=body) as r:
        if r.is_error:
            await r.aread()
            r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = (json.loads(line[6:]).get("choices") or [{}])[0].get("delta") or {}
            msg["content"] += delta.get("content") or ""
            for d in delta.get("tool_calls") or []:
                while len(msg["tool_calls"]) <= d.get("index", 0):
                    msg["tool_calls"].append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                call, fn = msg["tool_calls"][d.get("index", 0)], d.get("function") or {}
                call["id"] = d.get("id") or call["id"]
                call["function"]["name"] += fn.get("name") or ""
                call["function"]["arguments"] += fn.get("arguments") or ""
            yield msg
    yield msg


async def chat_stream(messages: list[dict], **params) -> AsyncIterator[str]:
    if SNAPDRAGON:
        from . import npu
        images = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages)
        role = "vision" if images else "assistant"
        async for event in npu.events(active(role), {"op": "chat", "messages": messages,
                                                   "params": {"temperature": .3, **params}, "stream": True}):
            if event["type"] == "token":
                yield event["text"]
        return
    body = {"messages": messages, "stream": True, "temperature": 0.3, **NO_THINK, **params}
    async with _http.stream("POST", f"{await url('assistant')}/chat/completions", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
            if delta:
                yield delta


async def embed(texts: list[str], query: bool = False) -> list[list[float]]:
    spec = active("embed")
    if SNAPDRAGON:
        from . import npu
        return await npu.embed(spec, texts, query)
    prefix = spec.query_prefix if query else spec.doc_prefix
    r = await _http.post(f"{await url('embed')}/embeddings", json={"input": [prefix + t for t in texts]})
    r.raise_for_status()
    return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


def status() -> dict[str, str]:
    """Per role: running (loaded now) | ready (downloaded, starts on demand) | missing."""
    up = runtime.running()
    if SNAPDRAGON:
        from . import npu
        up.update(npu.running())
    return {r: "running" if up.get(r) else "ready" if active(r).downloaded else "missing" for r in ROLES}
