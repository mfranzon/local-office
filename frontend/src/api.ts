export type DocStatus = "queued" | "processing" | "ready" | "error";

declare global {
  interface Window { localOffice?: { pickFolder: () => Promise<string | null> } }
}

export interface Doc {
  id: string;
  name: string;
  ext: string;
  size: number;
  created_at: string;
  status: DocStatus;
  pages_total: number;
  pages_done: number;
  error: string | null;
  wiki_status: "pending" | "processing" | "done" | "error";
  summary: string;
  source_id: number | null;
}

export interface SourceInfo {
  id: number; name: string; status: "idle" | "syncing" | "error"; error: string | null;
  last_sync: string | null; doc_count: number;
  config: { path: string };
}

export interface SkillInfo {
  name: string; description: string; instructions: string; builtin: boolean; enabled: boolean;
  references: string[]; ignored: string[];
}

export interface Step { tool: string; detail: string }

export interface ConnectionTool { name: string; title: string; description: string; read_only: boolean; enabled: boolean }
export interface Connection {
  id: string; name: string; url: string; enabled: boolean; error: string | null; checked_at?: string;
  server_name?: string; header_names: string[]; tools: ConnectionTool[];
}
export interface ExportFile { name: string; url: string }

export interface DeskSummary { id: string; name: string; kind: "doc" | "sheet" | "slides"; updated_at: string }
export type Cell = string | number | boolean | null;
/** The body is Markdown; the background is a colour as #rrggbb; the image is the name of a picture kept for the slides. */
export interface Slide { title: string; body: string; notes: string; background?: string; image?: string }
/** A file on the Desk. A document's content is Markdown; a sheet's is rows of cells as typed, with `values`
 *  holding what each cell shows (formulas computed); a presentation's is its slides. */
export type DeskFile = DeskSummary & { can_undo: boolean }
  & ({ kind: "doc"; content: string } | { kind: "sheet"; content: Cell[][]; values: Cell[][] } | { kind: "slides"; content: Slide[] });

export interface Page {
  n: number;
  text: string;
  method: "text" | "ocr";
  has_image: number;
}

export interface Source {
  n: number;
  kind: "doc" | "wiki" | "mcp";
  entity_id: number | null;
  doc_id: string | null;
  name: string;
  page: number | null;
  text: string;
}

export type EntityType = "organization" | "person" | "project";

export interface WikiEntitySummary {
  id: number; type: EntityType; name: string; summary: string; updated_at: string; doc_count: number;
}

export interface Fact {
  id: number; kind: "attr" | "role" | "relation" | "event" | "deadline"; key: string; value: string;
  date: string | null; page: number; doc_id: string; doc_name: string;
  target_id: number | null; target_name: string | null;
}

export interface WikiEntity extends WikiEntitySummary {
  summary_edited: number; notes: string; facts: Fact[]; conflict_keys: string[];
  backlinks: { id: number; name: string; label: string; doc_id: string; doc_name: string; page: number }[];
  documents: { id: string; name: string; doc_type: string | null; doc_date: string | null; summary: string }[];
}

export interface DocRef { doc_id: string; doc_name: string; page: number }

export type ReviewItem =
  | { kind: "conflict"; entity_id: number; name: string; key: string;
      options: { value: string; fact_ids: number[]; sources: DocRef[] }[] }
  | { kind: "duplicate"; a: { id: number; name: string }; b: { id: number; name: string }; same_id: boolean };

export interface Deadline extends DocRef {
  date: string; value: string; overdue: boolean; entities: { id: number; name: string }[];
}

export interface WikiOverview {
  entities: WikiEntitySummary[]; review: ReviewItem[]; deadlines: Deadline[]; pending_docs: number;
  log: { ts: string; action: string; detail: string }[];
}

export interface Msg {
  role: "user" | "assistant";
  content: string;
}

export interface ChatSummary { id: string; title: string; updated_at: string }

export interface SetupStatus {
  missing: { role: Role; name: string; size_mb: number }[];
  total_mb: number;
  ram_gb: number;
  chip: string;
  disk_free_gb: number;
}

export type Role = "assistant" | "ocr" | "embed" | "vision";

export interface ModelSpec {
  role: Role;
  ref: string;
  name: string;
  size_mb: number;
  tags: string[];
  downloaded: boolean;
  min_ram_gb: number;
  provider?: string;
}

export type ModelSettings = Record<Role, { active: ModelSpec; recommended: string; choices: ModelSpec[] }>
  & { busy: boolean; ram_gb: number; chip: string; disk_free_gb: number };

export type StreamEvent =
  | { type: "progress"; step: "pull" | "configure" | "test" | "reindex"; percent?: number; model?: string }
  | { type: "switched"; role: Role; model: ModelSpec }
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "skill"; name: string }
  | { type: "services" }
  | ({ type: "step" } & Step)
  | ({ type: "file" } & ExportFile)
  | { type: "edit"; slide?: number }
  | { type: "draft"; key: string; start: number; end: number; text: string }
  | { type: "done" };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try { detail = JSON.parse(body).detail ?? body; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : `${res.status}`);
  }
  return res.json();
}

const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then(json);

export const api = {
  status: () => fetch("/api/status").then(json<{ models: Record<string, "running" | "ready" | "missing">; connected: string[] }>),
  setup: () => fetch("/api/setup").then(json<SetupStatus>),
  documents: () => fetch("/api/documents").then(json<Doc[]>),
  document: (id: string) => fetch(`/api/documents/${id}`).then(json<Doc & { pages: Page[] }>),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return fetch("/api/documents", { method: "POST", body: form }).then(json<{ created: string[]; rejected: string[] }>);
  },
  remove: (id: string) => fetch(`/api/documents/${id}`, { method: "DELETE" }).then(json),
  retry: (id: string) => fetch(`/api/documents/${id}/retry`, { method: "POST" }).then(json),
  wiki: () => fetch("/api/wiki").then(json<WikiOverview>),
  entity: (id: number) => fetch(`/api/wiki/entities/${id}`).then(json<WikiEntity>),
  editEntity: (id: number, patch: { summary?: string; notes?: string }) =>
    fetch(`/api/wiki/entities/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(patch) }).then(json<WikiEntity>),
  deleteEntity: (id: number) => fetch(`/api/wiki/entities/${id}`, { method: "DELETE" }).then(json),
  rejectFact: (id: number) => fetch(`/api/wiki/facts/${id}/reject`, { method: "POST" }).then(json),
  resolveConflict: (entity_id: number, key: string, keep_fact_id: number) => post("/api/wiki/review/conflict", { entity_id, key, keep_fact_id }),
  merge: (keep_id: number, other_id: number) => post("/api/wiki/review/merge", { keep_id, other_id }),
  distinct: (keep_id: number, other_id: number) => post("/api/wiki/review/distinct", { keep_id, other_id }),
  rebuildWiki: () => post("/api/wiki/rebuild", {}),
  exportUrl: (lang: string) => `/api/wiki/export.zip?lang=${lang}`,
  sources: () => fetch("/api/sources").then(json<{ sources: SourceInfo[]; can_pick_folder: boolean }>).then(r =>
    ({ ...r, can_pick_folder: r.can_pick_folder || !!window.localOffice })),
  pickFolder: () => window.localOffice ? window.localOffice.pickFolder().then(path => ({ path }))
    : post("/api/sources/pick-folder", {}) as Promise<{ path: string | null }>,
  addFolder: (path: string) => post("/api/sources/folder", { path }),
  syncSource: (id: number) => post(`/api/sources/${id}/sync`, {}),
  removeSource: (id: number) => fetch(`/api/sources/${id}`, { method: "DELETE" }).then(json),
  skills: () => fetch("/api/skills").then(json<SkillInfo[]>),
  saveSkill: (name: string, description: string, instructions: string) =>
    fetch(`/api/skills/${name}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ description, instructions }) }).then(json<SkillInfo>),
  importSkill: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/skills/import", { method: "POST", body: form }).then(json<SkillInfo>);
  },
  toggleSkill: (name: string, enabled: boolean) => post(`/api/skills/${name}/enabled`, { enabled }),
  deleteSkill: (name: string) => fetch(`/api/skills/${name}`, { method: "DELETE" }).then(json),
  chats: () => fetch("/api/chats").then(json<ChatSummary[]>),
  chat: <T>(id: string) => fetch(`/api/chats/${id}`).then(json<ChatSummary & { turns: T[] }>),
  saveChat: (id: string, title: string, turns: unknown[]) =>
    fetch(`/api/chats/${id}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ title, turns }) }).then(json),
  deleteChat: (id: string) => fetch(`/api/chats/${id}`, { method: "DELETE" }).then(json),
  connections: () => fetch("/api/connections").then(json<Connection[]>),
  addConnection: (name: string, url: string, headers: Record<string, string>) =>
    post("/api/connections", { name, url, headers }) as Promise<Connection>,
  editConnection: (id: string, patch: { enabled?: boolean; tools?: Record<string, boolean>; headers?: Record<string, string> }) =>
    fetch(`/api/connections/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(patch) }).then(json<Connection>),
  refreshConnection: (id: string) => post(`/api/connections/${id}/refresh`, {}) as Promise<Connection>,
  removeConnection: (id: string) => fetch(`/api/connections/${id}`, { method: "DELETE" }).then(json),
  desk: () => fetch("/api/desk").then(json<DeskSummary[]>),
  deskFile: (id: string) => fetch(`/api/desk/${id}`).then(json<DeskFile>),
  deskNew: (name: string, kind: DeskSummary["kind"]) => post("/api/desk", { name, kind }) as Promise<DeskFile>,
  deskImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/desk/import", { method: "POST", body: form }).then(json<DeskFile>);
  },
  deskImage: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/desk/images", { method: "POST", body: form }).then(json<{ image: string }>);
  },
  deskImageUrl: (name: string) => `/api/desk/images/${name}`,
  deskCopy: (from: { doc_id: string } | { export: string }) => post("/api/desk/copy", from) as Promise<DeskFile>,
  deskSave: (id: string, patch: { name?: string; content?: string | Cell[][] | Slide[] }, keepalive = false) =>
    fetch(`/api/desk/${id}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(patch), keepalive }).then(json<DeskFile>),
  deskShift: (id: string, axis: "row" | "col", index: number, delta: number) =>
    post(`/api/desk/${id}/shift`, { axis, index, delta }) as Promise<DeskFile>,
  deskUndo: (id: string) => post(`/api/desk/${id}/undo`, {}) as Promise<DeskFile>,
  deskDelete: (id: string) => fetch(`/api/desk/${id}`, { method: "DELETE" }).then(json),
  deskExportUrl: (id: string, format: string) => `/api/desk/${id}/export?format=${format}`,
  modelSettings: () => fetch("/api/settings/models").then(json<ModelSettings>),
  pageUrl: (id: string, n: number) => `/api/documents/${id}/pages/${n}.jpg`,
  fileUrl: (id: string) => `/api/documents/${id}/file`,
};

/** POSTs and yields server-sent events until the stream closes. */
export async function* stream(url: string, body: BodyInit, signal: AbortSignal, headers?: HeadersInit) {
  const res = await fetch(url, { method: "POST", body, signal, headers });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${await res.text()}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    const parts = buf.split("\n\n");
    buf = parts.pop()!;
    for (const p of parts) if (p.startsWith("data: ")) yield JSON.parse(p.slice(6)) as StreamEvent;
  }
}
