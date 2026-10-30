import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Loader2, Plus, Trash2, X } from "lucide-react";
import { api, stream, type ChatSummary, type Doc, type Msg, type Source } from "../api";
import { useI18n } from "../i18n";
import Composer from "../components/Composer";
import Markdown from "../components/Markdown";

interface Turn extends Msg { sources?: Source[]; error?: string }

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

function when(ts: string, lang: string) {
  const d = new Date(ts.replace(" ", "T") + "Z");   // stored in UTC
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(lang, { day: "numeric", month: "short" });
}

export default function Ask({ docs, scope, setScope, onSource }: {
  docs: Doc[]; scope: Doc | null; setScope: (d: Doc | null) => void; onSource: (s: Source) => void;
}) {
  const { t, d, lang } = useI18n();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [history, setHistory] = useState<ChatSummary[]>([]);
  const shown = useRef<string | null>(null);   // the conversation on screen; a reply still streaming for another one stays off it
  const [phase, setPhase] = useState<"idle" | "searching" | "writing">("idle");
  const [useDocs, setUseDocs] = useState(true);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const hasDocs = docs.some((doc) => doc.pages_done > 0);
  const docsOn = useDocs && hasDocs;

  const loadHistory = useCallback(() => api.chats().then(setHistory).catch(() => {}), []);
  useEffect(() => { loadHistory(); }, [loadHistory]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const show = (id: string | null, ts: Turn[]) => { shown.current = id; setChatId(id); setTurns(ts); setPhase("idle"); };
  const newChat = () => { abort.current?.abort(); show(null, []); };
  const openChat = (id: string) => {
    abort.current?.abort();
    api.chat<Turn>(id).then((c) => show(id, c.turns)).catch(() => loadHistory());
  };
  const deleteChat = (c: ChatSummary) => {
    if (!confirm(t("confirm_delete_chat", { name: c.title }))) return;
    if (c.id === chatId) newChat();
    api.deleteChat(c.id).then(loadHistory);
  };

  const send = async (question: string) => {
    const id = chatId ?? newId();
    const messages: Msg[] = [...turns.map(({ role, content }) => ({ role, content })), { role: "user", content: question }];
    let mine: Turn[] = [...turns, { role: "user", content: question }, { role: "assistant", content: "" }];
    const update = (fn: (last: Turn) => Turn) => {
      mine = [...mine.slice(0, -1), fn(mine[mine.length - 1])];
      if (shown.current === id) setTurns(mine);
    };
    show(id, mine);
    setPhase(docsOn ? "searching" : "writing");
    abort.current = new AbortController();
    try {
      const body = JSON.stringify({ messages, use_docs: docsOn, doc_ids: scope ? [scope.id] : null });
      for await (const e of stream("/api/chat", body, abort.current.signal, { "content-type": "application/json" })) {
        if (e.type === "sources") { update((l) => ({ ...l, sources: e.sources })); setPhase("writing"); }
        if (e.type === "token") update((l) => ({ ...l, content: l.content + e.text }));
        if (e.type === "error") update((l) => ({ ...l, error: e.message }));
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") update((l) => ({ ...l, error: String(err) }));
    } finally {
      if (shown.current === id) setPhase("idle");
      const title = mine.find((m) => m.role === "user")!.content;
      api.saveChat(id, title, mine).then(loadHistory).catch(() => {});
    }
  };

  const cite = (turn: Turn, n: number) => {
    const s = turn.sources?.find((src) => src.n === n);
    if (s) onSource(s);
  };

  return (
    <div className="h-full flex">
      <aside className="w-64 shrink-0 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-3">
          <button onClick={newChat}
            className="w-full flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50">
            <Plus size={15} /> {t("new_chat")}
          </button>
        </div>
        <div className="px-5 mb-1 text-[11px] uppercase tracking-wide text-slate-400 font-semibold">{t("chats")}</div>
        <nav className="flex-1 overflow-auto px-3 pb-4">
          {!history.length && <p className="px-2 mt-2 text-sm text-slate-400">{t("no_chats")}</p>}
          {history.map((c) => (
            <div key={c.id} className={`group flex items-center rounded-lg ${c.id === chatId ? "bg-accent-soft text-accent" : "text-slate-700 hover:bg-slate-100"}`}>
              <button onClick={() => openChat(c.id)} className="flex-1 min-w-0 text-left px-3 py-2">
                <div className={`truncate text-sm ${c.id === chatId ? "font-medium" : ""}`}>{c.title}</div>
                <div className="text-xs text-slate-400">{when(c.updated_at, lang)}</div>
              </button>
              <button onClick={() => deleteChat(c)} title={t("delete_chat")}
                className="p-2 mr-1 rounded-md text-slate-400 hover:text-red-600 opacity-0 group-hover:opacity-100 focus:opacity-100">
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </nav>
      </aside>
      <div className="flex-1 min-w-0 h-full flex flex-col">
        <header className="flex items-center gap-3 px-8 py-4 border-b border-slate-200 bg-white">
          <h1 className="text-lg font-semibold">{t("ask_title")}</h1>
          {hasDocs && (
            <label className="flex items-center gap-2 text-sm text-slate-600 ml-4 cursor-pointer select-none">
              <input type="checkbox" checked={useDocs} onChange={(e) => setUseDocs(e.target.checked)} className="accent-[#2f5bd3] w-4 h-4" />
              {t("use_docs")}
            </label>
          )}
          {docsOn && (
            <span className="flex items-center gap-1.5 text-sm px-2.5 py-1 rounded-full bg-accent-soft text-accent">
              <FileText size={14} />
              <span className="max-w-64 truncate">{scope ? t("scope_one", { name: scope.name }) : t("scope_all")}</span>
              {scope && <button onClick={() => setScope(null)} className="hover:text-slate-900"><X size={14} /></button>}
            </span>
          )}
        </header>

        <div className="flex-1 overflow-auto">
          <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
            {!turns.length && (
              <div className="pt-10 text-center">
                {!hasDocs && <p className="text-slate-500 mb-6">{t("no_docs_hint")}</p>}
                <div className="grid grid-cols-2 gap-3 text-left">
                  {d.sugg.map((s) => (
                    <button key={s} onClick={() => send(s)}
                      className="px-4 py-3 rounded-xl border border-slate-200 bg-white text-[15px] text-slate-700 hover:border-accent hover:text-accent transition">{s}</button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((turn, i) => turn.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-accent text-white whitespace-pre-wrap">{turn.content}</div>
              </div>
            ) : (
              <div key={i} className="text-[15px]">
                {!turn.content && phase !== "idle" && i === turns.length - 1 && (
                  <div className="flex items-center gap-2 text-slate-500"><Loader2 size={16} className="animate-spin" /> {phase === "searching" ? t("searching") : t("thinking")}</div>
                )}
                <Markdown text={turn.content} onCite={turn.sources ? (n) => cite(turn, n) : undefined} />
                {turn.error && <div className="mt-2 px-3 py-2 rounded-lg bg-red-50 text-red-700 text-sm">{t("error")} {turn.error}</div>}
                {!!turn.sources?.length && turn.content && (
                  <div className="mt-3">
                    <div className="text-xs uppercase tracking-wide text-slate-400 mb-1.5">{t("sources")}</div>
                    <div className="flex flex-wrap gap-2">
                      {turn.sources.filter((s) => turn.content.includes(`[${s.n}]`)).map((s) => (
                        <button key={s.n} onClick={() => onSource(s)} title={s.text}
                          className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-sm hover:border-accent">
                          <span className="w-5 h-5 grid place-items-center rounded bg-accent-soft text-accent text-[11px] font-semibold">{s.n}</span>
                          {s.kind === "wiki" && <BookOpen size={14} className="text-violet-600" />}
                          <span className="max-w-56 truncate">{s.name}</span>
                          {s.kind === "wiki" ? <span className="text-violet-600 text-xs">Wiki</span> : <span className="text-slate-400">p. {s.page}</span>}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
            <div ref={bottom} />
          </div>
        </div>

        <div className="px-6 pb-6 pt-2">
          <div className="max-w-3xl mx-auto">
            <Composer placeholder={t("ask_placeholder")} busy={phase !== "idle"} onSend={send} onStop={() => abort.current?.abort()} />
          </div>
        </div>
      </div>
    </div>
  );
}
