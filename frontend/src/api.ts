export type DocStatus = "queued" | "processing" | "ready" | "error";

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
}

export interface Page {
  n: number;
  text: string;
  method: "text" | "ocr";
  has_image: number;
}

export interface Source {
  n: number;
  kind: "doc" | "wiki";
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

export type Role = "assistant" | "ocr" | "embed" | "agent";

export interface ModelSpec {
  role: Role;
  ref: string;
  name: string;
  size_mb: number;
  tags: string[];
  downloaded: boolean;
  min_ram_gb: number;
}

export type ModelSettings = Record<Role, { active: ModelSpec; recommended: string; choices: ModelSpec[] }>
  & { busy: boolean; ram_gb: number; chip: string; disk_free_gb: number };

export type StreamEvent =
  | { type: "progress"; step: "pull" | "configure" | "test" | "reindex"; percent?: number; model?: string }
  | { type: "switched"; role: Role; model: ModelSpec }
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then(json);

export const api = {
  status: () => fetch("/api/status").then(json<{ models: Record<string, "running" | "ready" | "missing"> }>),
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
  chats: () => fetch("/api/chats").then(json<ChatSummary[]>),
  chat: <T>(id: string) => fetch(`/api/chats/${id}`).then(json<ChatSummary & { turns: T[] }>),
  saveChat: (id: string, title: string, turns: unknown[]) =>
    fetch(`/api/chats/${id}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ title, turns }) }).then(json),
  deleteChat: (id: string) => fetch(`/api/chats/${id}`, { method: "DELETE" }).then(json),
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
