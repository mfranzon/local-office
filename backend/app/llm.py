"""Thin async client for the per-role llama-server instances. models.url() starts a role's server on
first use, so callers never need to know whether a model is loaded."""
import base64
import json
from collections.abc import AsyncIterator

import httpx

from . import runtime
from .config import ROLES
from .models import active, url

_http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5))
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


async def chat(role: str, messages: list[dict], **params) -> str:
    r = await _http.post(f"{await url(role)}/chat/completions", json={"messages": messages, "temperature": 0, **params})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


async def chat_stream(messages: list[dict], **params) -> AsyncIterator[str]:
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
    prefix = spec.query_prefix if query else spec.doc_prefix
    r = await _http.post(f"{await url('embed')}/embeddings", json={"input": [prefix + t for t in texts]})
    r.raise_for_status()
    return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


def status() -> dict[str, str]:
    """Per role: running (loaded now) | ready (downloaded, starts on demand) | missing."""
    up = runtime.running()
    return {r: "running" if up.get(r) else "ready" if active(r).downloaded else "missing" for r in ROLES}
