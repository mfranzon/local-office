import asyncio
import json

import httpx
import pytest

from app import agent, mcp, tools
from app import db as dbmod

TOOLS = [
    {"name": "search_mail", "description": "Search the mailbox.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "send_mail", "description": "Send an email.", "inputSchema": {"type": "object", "properties": {}}},
]


class FakeServer:
    """A Streamable HTTP MCP server: session id on initialize, tools/list as JSON, tools/call as SSE."""

    def __init__(self, token="secret"):
        self.token, self.seen = token, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        body = json.loads(request.content)
        self.seen.append((body.get("method"), request.headers.get("mcp-session-id")))
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": mcp.PROTOCOL, "serverInfo": {"name": "fake-mail"}, "capabilities": {"tools": {}}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, headers={"mcp-session-id": "s1"})
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": TOOLS}})
        if body["method"] == "tools/call":
            q = body["params"]["arguments"].get("q")
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": f"2 mails about {q}"}]}}
            sse = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress'})}\n\n" \
                  f"event: message\ndata: {json.dumps(msg)}\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "no method"}})


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    dbmod.init()
    fake = FakeServer()
    monkeypatch.setattr(mcp, "_http", httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    return fake


def add(token="secret"):
    return asyncio.run(mcp.add("Mail", "https://mail.example/mcp", {"Authorization": f"Bearer {token}"}))


def test_add_lists_tools_and_only_read_only_ones_start_on(server):
    s = add()
    assert s["error"] is None and s["server_name"] == "fake-mail"
    assert {t["name"]: t["enabled"] for t in s["tools"]} == {"search_mail": True, "send_mail": False}
    assert list(mcp.active()) == ["mail__search_mail"]
    assert mcp.view(s)["header_names"] == ["Authorization"] and "secret" not in json.dumps(mcp.view(s))
    assert ("notifications/initialized", "s1") in server.seen and ("tools/list", "s1") in server.seen


def test_toggles_survive_a_refresh(server):
    s = add()
    mcp.edit(s["id"], tools={"send_mail": True, "search_mail": False})
    asyncio.run(mcp.refresh(s["id"]))
    assert list(mcp.active()) == ["mail__send_mail"]
    mcp.edit(s["id"], enabled=False)
    assert mcp.active() == {} and mcp.schemas() == []


def test_bad_token_is_stored_as_an_error(server):
    s = add(token="wrong")
    assert "refused" in s["error"] and mcp.active() == {}


def test_tool_call_through_the_skill_tools(server):
    add()
    run = tools.Run()
    out = asyncio.run(tools.call(run, "mail__search_mail", {"q": "invoice 118"}))
    assert "2 mails about invoice 118" in out and out.startswith("[1]")
    asyncio.run(tools.call(run, "mail__search_mail", {"q": "other"}))
    assert [s["kind"] for s in run.sources] == ["mcp", "mcp"]   # each call is its own source
    assert asyncio.run(tools.call(run, "mail__send_mail", {})) == "Unknown tool mail__send_mail."   # off by default


@pytest.mark.parametrize("url", ["ftp://x", "http://mail.example/mcp", "mail.example"])
def test_urls_must_be_https(url):
    with pytest.raises(ValueError):
        mcp.check_url(url)
    assert mcp.check_url("http://localhost:3000/mcp")


def test_router_offers_connected_services(server, monkeypatch):
    add()
    seen = {}

    async def fake_chat(role, messages, **params):
        seen["enum"] = params["response_format"]["json_schema"]["schema"]["properties"]["skill"]["enum"]
        seen["prompt"] = messages[0]["content"]
        return json.dumps({"skill": agent.SERVICES})

    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    assert asyncio.run(agent.route("Any new mail from Rossi?", [])) == agent.SERVICES
    assert agent.SERVICES in seen["enum"] and "search_mail" in seen["prompt"]


def test_general_run_uses_service_tools(server, monkeypatch):
    add()
    replies = iter([
        {"content": "", "tool_calls": [{"id": "a", "function": {"name": "mail__search_mail", "arguments": '{"q": "Rossi"}'}}]},
        {"content": "Rossi wrote twice [1]."},
    ])

    async def fake_complete(role, messages, **params):
        assert any(t["function"]["name"] == "mail__search_mail" for t in params.get("tools", [{"function": {"name": "mail__search_mail"}}]))
        return next(replies)

    monkeypatch.setattr(agent.llm, "complete", fake_complete)

    async def collect():
        return [e async for e in agent.run(None, [{"role": "user", "content": "mail from Rossi?"}])]

    events = asyncio.run(collect())
    assert events[0] == {"type": "services"}
    assert {"type": "step", "tool": "mail__search_mail", "detail": "Rossi"} in events
    assert events[-1]["text"] == "Rossi wrote twice [1]."


def test_long_service_result_fits_the_context():
    """Documents read earlier are shortened, oldest first, so a long service result still fits; it stays whole."""
    docs = [{"role": "tool", "content": "d" * 5000} for _ in range(4)]
    web = {"role": "tool", "content": "w" * 8000}
    messages = [{"role": "system", "content": "s" * 2000}, *docs, web]
    agent._fit(messages, [])
    assert sum(len(m["content"]) for m in messages) <= (16384 // 2 - agent.ANSWER_TOKENS) * agent.CHARS_PER_TOKEN
    assert len(docs[0]["content"]) < 1000 and web["content"] == "w" * 8000
    assert docs[-1]["content"] == "d" * 5000          # shortened only as far as needed
