"""Connections to remote MCP servers (Model Context Protocol, Streamable HTTP transport).

A connected server's tools become extra tools for the assistant, in skills and in plain questions. Unlike the
rest of the app, these send data off the computer: the tool arguments the assistant writes (which can quote
documents) go to the server. So nothing is on by default: the user adds each server, and of its tools only the
ones the server marks read-only start enabled; any other tool (send, create, delete...) must be switched on.

Servers live in the settings table as a list of dicts: id, name, url, headers, enabled, tools, error.
"""
import json
import re
import uuid
from datetime import datetime

import httpx

from .db import db, get_setting, set_setting

PROTOCOL = "2025-06-18"
RESULT_CHARS = 8000
SEP = "__"                     # model-facing tool name: <server slug>__<tool name>
_http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=True)


class MCPError(Exception):
    pass


class Session:
    """One MCP session: initialize once, then send requests. Responses may be plain JSON or an SSE stream."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url, self.headers, self.sid, self.n = url, dict(headers or {}), None, 0

    async def _post(self, body: dict) -> dict | None:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": PROTOCOL, **self.headers}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        async with _http.stream("POST", self.url, json=body, headers=headers) as r:
            if r.status_code in (401, 403):
                raise MCPError(f"the server refused access ({r.status_code}): check the token")
            if r.status_code >= 400:
                raise MCPError(f"the server answered {r.status_code}: {(await r.aread()).decode(errors='replace')[:200]}")
            self.sid = r.headers.get("mcp-session-id", self.sid)
            if "id" not in body:
                return None
            if r.headers.get("content-type", "").startswith("text/event-stream"):
                async for msg in _sse_messages(r.aiter_lines()):
                    if msg.get("id") == body["id"]:
                        return _result(msg)
                raise MCPError("the server closed the stream without an answer")
            return _result(json.loads(await r.aread()))

    async def request(self, method: str, params: dict | None = None) -> dict:
        if self.sid is None and method != "initialize":
            await self.initialize()
        self.n += 1
        return await self._post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})

    async def initialize(self) -> dict:
        self.sid = ""          # marks the session as started, even if the server does not use session ids
        info = await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                 "clientInfo": {"name": "Local Office", "version": "0.2"}})
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return info

    async def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            page = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor or len(tools) > 500:
                return tools

    async def call_tool(self, name: str, args: dict) -> str:
        out = await self.request("tools/call", {"name": name, "arguments": args})
        text = "\n\n".join(_content_text(c) for c in out.get("content", []))
        if not text and out.get("structuredContent") is not None:
            text = json.dumps(out["structuredContent"], ensure_ascii=False)
        if out.get("isError"):
            return f"Error from the service: {text or 'unknown error'}"
        return text[:RESULT_CHARS] + ("\n(truncated)" if len(text) > RESULT_CHARS else "") or "(empty result)"


async def _sse_messages(lines):
    data: list[str] = []
    async for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            try:
                yield json.loads("\n".join(data))
            except json.JSONDecodeError:
                pass
            data = []


def _result(msg: dict) -> dict:
    if "error" in msg:
        raise MCPError(str(msg["error"].get("message", msg["error"]))[:300])
    return msg.get("result") or {}


def _content_text(c: dict) -> str:
    if c.get("type") == "text":
        return c.get("text", "")
    if c.get("type") == "resource":
        r = c.get("resource", {})
        return r.get("text") or f"(file {r.get('uri', '')})"
    if c.get("type") == "resource_link":
        return f"(link {c.get('name', '')}: {c.get('uri', '')})"
    return f"({c.get('type', 'unknown')} content, not shown)"


# --- configured servers ---

def servers() -> list[dict]:
    with db() as conn:
        return get_setting(conn, "mcp.servers", [])


def _save(items: list[dict]):
    with db() as conn:
        set_setting(conn, "mcp.servers", items)


def get(server_id: str) -> dict | None:
    return next((s for s in servers() if s["id"] == server_id), None)


def view(s: dict) -> dict:
    """What the UI sees: header values are secrets, so only their names are shown."""
    return {**{k: v for k, v in s.items() if k != "headers"}, "header_names": sorted(s.get("headers", {}))}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:20] or "server"


def check_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"https?://[^\s/]+", url):
        raise ValueError("the address must start with https://")
    if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])([:/]|$)", url):
        raise ValueError("use https:// (plain http is only allowed for localhost)")
    return url


def _merge_tools(old: list[dict], fresh: list[dict]) -> list[dict]:
    """Keeps the user's on/off choices; a new tool is on only if the server says it is read-only."""
    before = {t["name"]: t["enabled"] for t in old}
    out = []
    for t in fresh:
        read_only = bool((t.get("annotations") or {}).get("readOnlyHint"))
        out.append({"name": t["name"], "title": t.get("title") or (t.get("annotations") or {}).get("title") or "",
                    "description": (t.get("description") or "")[:1024],
                    "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
                    "read_only": read_only, "enabled": before.get(t["name"], read_only)})
    return out


async def refresh(server_id: str) -> dict:
    """Connects and (re)reads the server's tools. Errors are stored on the server and shown in Settings."""
    s = get(server_id)
    if s is None:
        raise KeyError(server_id)
    try:
        session = Session(s["url"], s.get("headers"))
        info = await session.initialize()
        tools = await session.list_tools()
        update = {"tools": _merge_tools(s.get("tools", []), tools), "error": None,
                  "server_name": (info.get("serverInfo") or {}).get("name", ""),
                  "instructions": (info.get("instructions") or "")[:1000]}
    except (MCPError, httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e:
        update = {"error": str(e) or type(e).__name__}
    update["checked_at"] = datetime.now().isoformat(timespec="seconds")
    return _update(server_id, update)


def _update(server_id: str, patch: dict) -> dict:
    items = servers()
    for s in items:
        if s["id"] == server_id:
            s.update(patch)
            _save(items)
            return s
    raise KeyError(server_id)


async def add(name: str, url: str, headers: dict[str, str]) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("a name is required")
    url = check_url(url)
    taken = {_slug(s["name"]) for s in servers()}
    if _slug(name) in taken:
        raise ValueError(f"a server named {name} already exists")
    s = {"id": uuid.uuid4().hex[:10], "name": name, "url": url, "enabled": True, "tools": [], "error": None,
         "headers": {k.strip(): v.strip() for k, v in headers.items() if k.strip() and v.strip()}}
    _save([*servers(), s])
    return await refresh(s["id"])


def edit(server_id: str, enabled: bool | None = None, tools: dict[str, bool] | None = None,
         headers: dict[str, str] | None = None) -> dict:
    s = get(server_id)
    if s is None:
        raise KeyError(server_id)
    patch: dict = {}
    if enabled is not None:
        patch["enabled"] = enabled
    if tools:
        patch["tools"] = [{**t, "enabled": tools.get(t["name"], t["enabled"])} for t in s["tools"]]
    if headers is not None:   # replaces all headers; an empty value removes that header
        patch["headers"] = {k.strip(): v.strip() for k, v in headers.items() if k.strip() and v.strip()}
    return _update(server_id, patch)


def remove(server_id: str) -> bool:
    items = servers()
    kept = [s for s in items if s["id"] != server_id]
    _save(kept)
    return len(kept) < len(items)


# --- tools for the assistant ---

def active() -> dict[str, tuple[dict, dict]]:
    """Model-facing tool name -> (server, tool), for every enabled tool of every enabled, working server."""
    out = {}
    for s in servers():
        if s.get("enabled") and not s.get("error"):
            for t in s.get("tools", []):
                if t["enabled"]:
                    out[f"{_slug(s['name'])}{SEP}{re.sub(r'[^A-Za-z0-9_-]', '_', t['name'])}"[:64]] = (s, t)
    return out


def schemas() -> list[dict]:
    return [{"type": "function", "function": {
        "name": name, "description": f"[{s['name']}, external service] {t['description'] or t['title'] or t['name']}",
        "parameters": t["input_schema"]}} for name, (s, t) in active().items()]


def summary() -> str:
    """One line per enabled server, for the router: what can be asked of it."""
    by_server: dict[str, list[str]] = {}
    for s, t in active().values():
        by_server.setdefault(s["name"], []).append(t["name"])
    return "\n".join(f"- {name}: {', '.join(tools[:12])}" for name, tools in by_server.items())


class Calls:
    """Sessions reused within one assistant run."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}

    async def call(self, name: str, args: dict) -> tuple[dict, dict, str]:
        server, tool = active()[name]
        session = self.sessions.setdefault(server["id"], Session(server["url"], server.get("headers")))
        try:
            return server, tool, await session.call_tool(tool["name"], args)
        except (MCPError, httpx.HTTPError, json.JSONDecodeError) as e:
            self.sessions.pop(server["id"], None)   # a stale session is opened again on the next call
            return server, tool, f"Error: could not reach {server['name']}: {e or type(e).__name__}"
