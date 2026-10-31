import asyncio
import io
import json
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import agent, ingest, llm, models, rag, runtime, skills, sources, tools, wiki, wiki_views
from .config import ROLES, STATIC_DIR
from .db import db, init


@asynccontextmanager
async def lifespan(_):
    init()
    models.load()
    ingest.FILES.mkdir(parents=True, exist_ok=True)
    ingest.resume_pending()
    wiki.resume_pending()
    tasks = [asyncio.create_task(ingest.worker()), asyncio.create_task(wiki.worker()), asyncio.create_task(runtime.reaper()),
             asyncio.create_task(sources.worker())]
    yield
    for t in tasks:
        t.cancel()
    runtime.shutdown()


app = FastAPI(title="Local Office", lifespan=lifespan)


def sse(events):
    async def gen():
        try:
            async for e in events:
                yield f"data: {json.dumps(e)}\n\n"
        except Exception as e:  # surface model errors in the UI instead of a silently dead stream
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# --- status ---

@app.get("/api/status")
async def status():
    with db() as conn:
        counts = dict(conn.execute("SELECT status, count(*) FROM documents GROUP BY status").fetchall())
    return {"models": llm.status(), "documents": counts}


# --- first run: download the default models ---

@app.get("/api/setup")
def setup_status():
    todo = models.missing()
    return {"missing": [{"role": s.role, "name": s.name, "size_mb": s.size_mb} for s in todo],
            "total_mb": sum(s.size_mb for s in todo), "ram_gb": models.HOST_RAM_GB, "chip": models.HOST_CHIP,
            "disk_free_gb": models.free_disk_gb()}


@app.post("/api/setup/download")
async def setup_download():
    if models.switch_lock.locked():
        raise HTTPException(409, "a download is already in progress")

    async def events():
        async with models.switch_lock:
            async for e in models.download_missing():
                yield e
    return sse(events())


# --- documents ---

def _doc_or_404(conn, doc_id: str):
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@app.get("/api/documents")
def list_documents():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY created_at DESC, rowid DESC")]


@app.post("/api/documents")
def upload(files: list[UploadFile] = File(...)):
    created, rejected = [], []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ingest.SUPPORTED:
            rejected.append(f.filename)
            continue
        created.append(ingest.add(Path(f.filename).name, ext, src=f.file))
    return {"created": created, "rejected": rejected}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    with db() as conn:
        doc = dict(_doc_or_404(conn, doc_id))
        doc["pages"] = [dict(r) for r in conn.execute(
            "SELECT n, text, method, has_image FROM pages WHERE doc_id=? ORDER BY n", (doc_id,))]
    return doc


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    with db() as conn:
        _doc_or_404(conn, doc_id)
        cited = ingest.remove(conn, [doc_id])
    asyncio.create_task(wiki.refresh_summaries(cited))
    return {"ok": True}


@app.post("/api/documents/{doc_id}/retry")
def retry(doc_id: str):
    with db() as conn:
        _doc_or_404(conn, doc_id)
        conn.execute("UPDATE documents SET status='queued', error=NULL WHERE id=?", (doc_id,))
    ingest.enqueue(doc_id)
    return {"ok": True}


@app.get("/api/documents/{doc_id}/pages/{n}.jpg")
def page_image(doc_id: str, n: int):
    path = ingest.page_image_path(doc_id, n)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/documents/{doc_id}/file")
def original_file(doc_id: str):
    with db() as conn:
        doc = _doc_or_404(conn, doc_id)
    return FileResponse(ingest.file_path(doc_id, doc["ext"]), filename=doc["name"])


# --- connected folders (read-only) ---

class FolderSource(BaseModel):
    path: str


@app.get("/api/sources")
def list_sources():
    return {"sources": sources.listing(), "can_pick_folder": sources.pick_folder is not None}


@app.post("/api/sources/pick-folder")
def pick_folder():
    if sources.pick_folder is None:
        raise HTTPException(501, "folder picker is only available in the app")
    return {"path": sources.pick_folder()}


@app.post("/api/sources/folder")
async def add_folder(req: FolderSource):
    path = Path(req.path).expanduser()
    if not path.is_dir():
        raise HTTPException(400, "folder not found")
    with db() as conn:
        for r in conn.execute("SELECT config FROM sources"):
            other = Path(json.loads(r[0])["path"])
            if path == other or other in path.parents or path in other.parents:
                raise HTTPException(400, f"overlaps a folder that is already connected: {other}")
    source_id = sources.create(path.name, {"path": str(path)})
    asyncio.create_task(sources.sync(source_id))
    return {"id": source_id}


@app.post("/api/sources/{source_id}/sync")
async def sync_source(source_id: int):
    asyncio.create_task(sources.sync(source_id))
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: int):
    cited = sources.delete(source_id)
    asyncio.create_task(wiki.refresh_summaries(cited))
    return {"ok": True}


# --- chat over documents ---

class ChatRequest(BaseModel):
    messages: list[dict]
    use_docs: bool = True
    doc_ids: list[str] | None = None


async def _pick_skill(req: ChatRequest) -> skills.Skill | None:
    """'/name request' runs that skill; otherwise the assistant may route the message to an enabled skill."""
    last = req.messages[-1]
    if last["role"] == "user" and (m := re.match(r"/([a-z0-9-]+)\s*(.*)", last["content"], re.S)):
        skill = skills.get(m[1])
        if skill and skill.enabled:
            last["content"] = m[2].strip() or skill.description
            return skill
    if req.use_docs and not req.doc_ids:
        return await agent.route(last["content"], skills.enabled())
    return None


@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def events():
        if skill := await _pick_skill(req):
            async for e in agent.run(skill, req.messages):
                yield e
            return
        hits = None
        if req.use_docs:
            question = next(m["content"] for m in reversed(req.messages) if m["role"] == "user")
            pages = [] if req.doc_ids else wiki_views.context_for(question)
            hits = pages + await rag.search(question, req.doc_ids, k=8 - min(len(pages), 3))
            yield {"type": "sources", "sources": [
                {"n": i, "kind": h.get("kind", "doc"), "entity_id": h.get("entity_id"), "doc_id": h.get("doc_id"),
                 "name": h["name"], "page": h.get("page"), "text": h["text"][:400]}
                for i, h in enumerate(hits, 1)]}
        async for token in llm.chat_stream(rag.build_messages(req.messages, hits)):
            yield {"type": "token", "text": token}
    return sse(events())


# --- skills ---

class SkillSave(BaseModel):
    description: str
    instructions: str


class SkillToggle(BaseModel):
    enabled: bool


@app.get("/api/skills")
def list_skills():
    return [s.view() for s in skills.all_skills()]


@app.put("/api/skills/{name}")
def save_skill(name: str, body: SkillSave):
    try:
        return skills.save(name, body.description, body.instructions).view()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/skills/import")
def import_skill(file: UploadFile = File(...)):
    try:
        return skills.import_file(file.filename or "SKILL.md", file.file.read()).view()
    except (ValueError, zipfile.BadZipFile, UnicodeDecodeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/skills/{name}/enabled")
def toggle_skill(name: str, body: SkillToggle):
    skills.set_enabled(name, body.enabled)
    return {"ok": True}


@app.delete("/api/skills/{name}")
def delete_skill(name: str):
    if not skills.delete(name):
        raise HTTPException(400, "only skills you added can be deleted")
    return {"ok": True}


@app.get("/api/exports/{run_id}/{filename}")
def download_export(run_id: str, filename: str):
    path = (tools.EXPORTS / run_id / filename).resolve()
    if tools.EXPORTS.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=filename)


# --- saved conversations ---

class ChatSave(BaseModel):
    title: str
    turns: list[dict]


@app.get("/api/chats")
def list_chats():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, title, updated_at FROM chats ORDER BY updated_at DESC")]


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "chat not found")
    return {**dict(row), "turns": json.loads(row["turns"])}


@app.put("/api/chats/{chat_id}")
def save_chat(chat_id: str, chat: ChatSave):
    with db() as conn:
        conn.execute("""INSERT INTO chats(id, title, turns) VALUES (?,?,?)
                        ON CONFLICT(id) DO UPDATE SET title=excluded.title, turns=excluded.turns, updated_at=datetime('now')""",
                     (chat_id, chat.title.strip()[:120], json.dumps(chat.turns)))
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    with db() as conn:
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    return {"ok": True}


# --- settings: model per role ---

@app.get("/api/settings/models")
async def model_settings():
    return {**models.overview(), "busy": models.switch_lock.locked()}


class SwitchRequest(BaseModel):
    role: str
    ref: str


@app.post("/api/settings/models")
async def switch_model(req: SwitchRequest):
    if req.role not in ROLES or not req.ref.strip():
        raise HTTPException(400, "unknown role or empty model")
    if models.switch_lock.locked():
        raise HTTPException(409, "another model change is in progress")

    async def events():
        async with models.switch_lock:
            async for e in models.switch(req.role, req.ref):
                yield e
    return sse(events())


# --- wiki ---

@app.get("/api/wiki")
def wiki_overview():
    with db() as conn:
        return wiki_views.overview(conn)


@app.get("/api/wiki/entities/{eid}")
def wiki_entity(eid: int):
    with db() as conn:
        e = wiki_views.entity_detail(conn, eid)
    if e is None:
        raise HTTPException(404, "entity not found")
    return e


class EntityPatch(BaseModel):
    summary: str | None = None      # "" = back to the automatic summary
    notes: str | None = None


@app.patch("/api/wiki/entities/{eid}")
async def wiki_edit(eid: int, patch: EntityPatch):
    with db() as conn:
        e = conn.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone() or _404()
        if patch.notes is not None:
            conn.execute("UPDATE entities SET notes=? WHERE id=?", (patch.notes, eid))
        if patch.summary is not None:
            edited = bool(patch.summary.strip())
            conn.execute("UPDATE entities SET summary=?, summary_edited=?, summary_stale=? WHERE id=?",
                         (patch.summary.strip(), edited, not edited, eid))
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('edit', ?)", (e["name"],))
    if patch.summary == "":
        await wiki.refresh_summaries([eid])
    return wiki_entity(eid)


@app.delete("/api/wiki/entities/{eid}")
def wiki_delete(eid: int):
    """Removes the page and stops it from being re-created from documents."""
    with db() as conn:
        e = conn.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone() or _404()
        conn.execute("INSERT OR IGNORE INTO ignored_names(norm) SELECT norm FROM aliases WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('delete', ?)", (e["name"],))
    return {"ok": True}


@app.post("/api/wiki/facts/{fact_id}/reject")
def wiki_reject(fact_id: int):
    with db() as conn:
        f = conn.execute("SELECT f.*, e.name FROM facts f JOIN entities e ON e.id=f.entity_id WHERE f.id=?", (fact_id,)).fetchone() or _404()
        _reject(conn, f["entity_id"], f["key"], f["norm"])
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('reject', ?)", (f"{f['name']}: {f['key']} = {f['value']}",))
    return {"ok": True}


def _reject(conn, eid: int, key: str, norm: str):
    conn.execute("UPDATE facts SET status='rejected' WHERE entity_id=? AND key=? AND norm=?", (eid, key, norm))
    conn.execute("INSERT OR IGNORE INTO rejected_facts(entity_id, key, norm) VALUES (?,?,?)", (eid, key, norm))
    conn.execute("UPDATE entities SET summary_stale=1 WHERE id=?", (eid,))


class Resolve(BaseModel):
    entity_id: int
    key: str
    keep_fact_id: int


@app.post("/api/wiki/review/conflict")
def wiki_resolve(r: Resolve):
    with db() as conn:
        keep = conn.execute("SELECT norm, value FROM facts WHERE id=? AND entity_id=? AND key=?",
                            (r.keep_fact_id, r.entity_id, r.key)).fetchone() or _404()
        for other in conn.execute("SELECT DISTINCT norm FROM facts WHERE entity_id=? AND key=? AND norm!=? AND status='ok'",
                                  (r.entity_id, r.key, keep["norm"])).fetchall():
            _reject(conn, r.entity_id, r.key, other["norm"])
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('resolve', ?)", (f"#{r.entity_id} {r.key} = {keep['value']}",))
    return {"ok": True}


class Pair(BaseModel):
    keep_id: int
    other_id: int


@app.post("/api/wiki/review/merge")
async def wiki_merge(p: Pair):
    with db() as conn:
        keep = conn.execute("SELECT name FROM entities WHERE id=?", (p.keep_id,)).fetchone() or _404()
        other = conn.execute("SELECT name, notes FROM entities WHERE id=?", (p.other_id,)).fetchone() or _404()
        conn.execute("UPDATE facts SET entity_id=? WHERE entity_id=?", (p.keep_id, p.other_id))
        conn.execute("UPDATE facts SET target_id=? WHERE target_id=?", (p.keep_id, p.other_id))
        conn.execute("UPDATE aliases SET entity_id=? WHERE entity_id=?", (p.keep_id, p.other_id))
        if other["notes"].strip():
            conn.execute("UPDATE entities SET notes = notes || char(10) || ? WHERE id=?", (other["notes"], p.keep_id))
        conn.execute("DELETE FROM entities WHERE id=?", (p.other_id,))
        conn.execute("UPDATE entities SET summary_stale=1, updated_at=datetime('now') WHERE id=?", (p.keep_id,))
        conn.execute("INSERT INTO wiki_log(action, detail) VALUES ('merge', ?)", (f"{other['name']} -> {keep['name']}",))
    await wiki.refresh_summaries([p.keep_id])
    return {"ok": True}


@app.post("/api/wiki/review/distinct")
def wiki_distinct(p: Pair):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO not_duplicates(a, b) VALUES (?,?)",
                     (min(p.keep_id, p.other_id), max(p.keep_id, p.other_id)))
    return {"ok": True}


@app.post("/api/wiki/rebuild")
def wiki_rebuild():
    with db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE status='ready' ORDER BY created_at")]
    for doc_id in ids:
        wiki.enqueue(doc_id)
    return {"queued": len(ids)}


@app.get("/api/wiki/export.zip")
def wiki_export(lang: str = "it"):
    data = wiki_views.export_zip("en" if lang == "en" else "it")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="local-office-wiki.zip"'})


def _404():
    raise HTTPException(404, "not found")


# --- image analysis ---

def _to_jpeg(data: bytes) -> bytes:
    img = ingest._fit(Image.open(io.BytesIO(data)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@app.post("/api/vision")
async def vision(image: UploadFile = File(...), messages: str = Form(...)):
    try:
        jpeg = await asyncio.to_thread(_to_jpeg, await image.read())
    except Exception:
        raise HTTPException(400, "unsupported image")
    history = json.loads(messages)
    first = history[0]
    history[0] = {"role": "user", "content": [llm.image_part(jpeg), {"type": "text", "text": first["content"]}]}

    async def events():
        msgs = [{"role": "system", "content": rag.SYSTEM_PLAIN}, *history]
        async for token in llm.chat_stream(msgs):
            yield {"type": "token", "text": token}
    return sse(events())


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
