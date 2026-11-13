import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, BookOpen, Download, FileSpreadsheet, FileText, FolderOpen, Loader2, PanelRight, Plug, Presentation, Sparkles, Trash2, Undo2 } from "lucide-react";
import { api, stream, type Cell, type DeskFile, type DeskSummary, type Doc, type Msg, type Slide, type Source, type Step } from "../api";
import { useI18n } from "../i18n";
import Composer from "../components/Composer";
import DocEditor, { type DocHandle, type Typing } from "../components/DocEditor";
import { blocks } from "../desk/markdown";
import Markdown from "../components/Markdown";
import SheetEditor, { type SheetHandle } from "../components/SheetEditor";
import SlidesEditor, { type SlidesHandle } from "../components/SlidesEditor";
import Steps from "../components/Steps";
import { when } from "./Ask";

interface Turn extends Msg { sources?: Source[]; error?: string; steps?: Step[] }
type Kind = DeskSummary["kind"];
const KINDS = {
  doc: { Icon: FileText, color: "text-accent", formats: [["docx", "Word"], ["md", "Markdown"]] },
  sheet: { Icon: FileSpreadsheet, color: "text-emerald-600", formats: [["xlsx", "Excel"], ["csv", "CSV"]] },
  slides: { Icon: Presentation, color: "text-orange-600", formats: [["pptx", "PowerPoint"]] },
} as const;
const OPENS = ".docx,.xlsx,.pptx,.csv,.md,.txt";

function KindIcon({ kind, size = 20 }: { kind: Kind; size?: number }) {
  const { Icon, color } = KINDS[kind];
  return <Icon size={size} className={`${color} shrink-0`} />;
}

/** Until the server answers with the computed values, an edited cell shows what was typed. */
function provisional(rows: Cell[][], before: Cell[][], values: Cell[][]): Cell[][] {
  return rows.map((row, r) => row.map((v, c) =>
    v === (before[r]?.[c] ?? null) ? values[r]?.[c] ?? null : typeof v === "string" && v.startsWith("=") ? null : v));
}

function Assistant({ turns, kind, working, busy, docs, onSend, onStop, onSource }: {
  turns: Turn[]; kind: Kind; working: boolean; busy: boolean; docs: Doc[];
  onSend: (text: string) => void; onStop: () => void; onSource: (s: Source) => void;
}) {
  const { t, d } = useI18n();
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);
  return (
    <aside className="w-[22rem] shrink-0 border-l border-slate-200 bg-white flex flex-col">
      <div className="px-4 py-3 flex items-center gap-2 font-medium border-b border-slate-100"><Sparkles size={16} className="text-accent" /> {t("desk_assistant")}</div>
      <div className="flex-1 overflow-auto px-4 py-4 space-y-4 text-[14px]">
        {!turns.length && (
          <div>
            <p className="text-slate-500 text-sm">{t("desk_assistant_hint")}</p>
            <div className="mt-4 space-y-2">
              {d[`desk_sugg_${kind}`].map((s) => (
                <button key={s} disabled={busy} onClick={() => onSend(s)}
                  className="w-full text-left px-3 py-2 rounded-xl border border-slate-200 text-slate-700 hover:border-accent hover:text-accent transition disabled:opacity-40">{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn, i) => turn.role === "user" ? (
          <div key={i} className="flex justify-end">
            <div className="max-w-[90%] px-3 py-2 rounded-2xl rounded-br-md bg-accent text-white whitespace-pre-wrap">{turn.content}</div>
          </div>
        ) : (
          <div key={i}>
            {!!turn.steps?.length && <Steps steps={turn.steps} docs={docs} running={working && !turn.content && i === turns.length - 1} />}
            {working && !turn.content && !turn.steps?.length && i === turns.length - 1 && (
              <div className="flex items-center gap-2 text-slate-500"><Loader2 size={15} className="animate-spin" /> {t("thinking")}</div>
            )}
            <div className="mt-2"><Markdown text={turn.content} onCite={turn.sources ? (n) => { const s = turn.sources!.find((x) => x.n === n); if (s) onSource(s); } : undefined} /></div>
            {turn.error && <div className="mt-2 px-3 py-2 rounded-lg bg-red-50 text-red-700 text-sm">{t("error")} {turn.error}</div>}
            {turn.content && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {turn.sources?.filter((s) => turn.content.includes(`[${s.n}]`)).map((s) => (
                  <button key={s.n} onClick={() => onSource(s)} title={s.text}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-lg border border-slate-200 text-xs hover:border-accent">
                    <span className="w-4 h-4 grid place-items-center rounded bg-accent-soft text-accent text-[10px] font-semibold">{s.n}</span>
                    {s.kind === "wiki" && <BookOpen size={12} className="text-violet-600" />}
                    {s.kind === "mcp" && <Plug size={12} className="text-amber-700" />}
                    <span className="max-w-40 truncate">{s.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <div className="p-3">
        <Composer placeholder={t("desk_ask_placeholder")} busy={busy} onSend={onSend} onStop={onStop} />
      </div>
    </aside>
  );
}

/** The Desk: files written in the app. A file opens in a page, a grid or a deck of slides, with the assistant next to it;
 *  what the user types is saved as they go, and what the assistant changes appears in the file. */
export default function Desk({ docs, open, onSource }: { docs: Doc[]; open: { id: string } | null; onSource: (s: Source) => void }) {
  const { t, lang } = useI18n();
  const [files, setFiles] = useState<DeskSummary[]>([]);
  const [file, setFile] = useState<DeskFile | null>(null);
  const [rev, setRev] = useState(0);                                 // goes up when the content comes from the server, not from typing
  const [saving, setSaving] = useState<"saved" | "saving" | "error">("saved");
  const [chats, setChats] = useState<Record<string, Turn[]>>({});   // one conversation per file, until the app closes
  const [busy, setBusy] = useState<string | null>(null);             // the file the assistant is working on
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [typing, setTyping] = useState<Typing | null>(null);         // the page while the assistant writes an edit
  const draft = useRef<{ key: string; base: string[]; start: number; end: number; text: string; md: string } | null>(null);
  const docText = useRef("");                                        // the open document as it is now, typing included
  const pending = useRef<{ id: string; content: string | Cell[][] | Slide[] } | null>(null);
  const timer = useRef<number>();
  const saves = useRef(0);
  const shownId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const page = useRef<DocHandle>(null);
  const grid = useRef<SheetHandle>(null);
  const deck = useRef<SlidesHandle>(null);

  const refresh = useCallback(() => api.desk().then(setFiles).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh]);

  const show = useCallback((f: DeskFile | null) => {
    shownId.current = f?.id ?? null;
    if (f?.kind === "doc") docText.current = f.content;
    draft.current = null;
    setTyping(null);
    setFile(f);
    setRev((r) => r + 1);
  }, []);
  const fail = (e: unknown) => setError((e as Error).message);

  /** Sends what was typed since the last save. */
  const flush = useCallback(async (keepalive = false) => {
    clearTimeout(timer.current);
    const p = pending.current;
    if (!p) return;
    pending.current = null;
    const n = ++saves.current;
    try {
      const saved = await api.deskSave(p.id, { content: p.content }, keepalive && JSON.stringify(p.content).length < 60000);
      if (n !== saves.current || pending.current) return;            // typed again meanwhile: that save will report
      setSaving("saved");
      if (saved.kind === "sheet") setFile((f) => f?.id === saved.id ? saved : f);   // formulas computed, cells as stored
    } catch { setSaving("error"); }
  }, []);
  const changed = (id: string, content: string | Cell[][] | Slide[], now = false) => {
    if (typeof content === "string") docText.current = content;
    pending.current = { id, content };
    setSaving("saving");
    clearTimeout(timer.current);
    if (now) flush(); else timer.current = window.setTimeout(flush, 600);
  };
  useEffect(() => {   // closing the window right after typing still saves
    const leave = () => { flush(true); };
    window.addEventListener("pagehide", leave);
    return () => window.removeEventListener("pagehide", leave);
  }, [flush]);

  const openFile = useCallback(async (id: string | null) => {
    await flush();
    setError(null);
    try { show(id ? await api.deskFile(id) : null); } catch (e) { fail(e); }
    refresh();
  }, [flush, show, refresh]);
  useEffect(() => { if (open) openFile(open.id); }, [open, openFile]);

  const create = (kind: Kind) => api.deskNew(t(`desk_untitled_${kind}`), kind).then(show).then(refresh).catch(fail);
  const importFile = (f: File | undefined) => { if (f) { setError(null); api.deskImport(f).then(show).then(refresh).catch(fail); } };
  const remove = (f: DeskSummary) => {
    if (confirm(t("confirm_delete", { name: f.name }))) api.deskDelete(f.id).then(refresh).catch(fail);
  };
  const rename = (name: string) => {
    if (file && name.trim() && name.trim() !== file.name) api.deskSave(file.id, { name }).then((f) => setFile((cur) => cur && { ...cur, name: f.name })).then(refresh).catch(fail);
  };
  const download = async (format: string) => {
    await flush();
    const a = document.createElement("a");
    a.href = api.deskExportUrl(file!.id, format);
    a.download = "";
    a.click();
  };
  const undoAssistant = async () => { await flush(); api.deskUndo(file!.id).then(show).catch(fail); };
  const shift = async (axis: "row" | "col", index: number, delta: number) => {
    await flush();
    api.deskShift(file!.id, axis, index, delta).then(setFile).catch(fail);
  };

  const send = async (text: string) => {
    if (!file || busy) return;
    const id = file.id;
    await flush();                                                   // the assistant reads the file as it is on screen
    const selection = (file.kind === "doc" ? page : file.kind === "sheet" ? grid : deck).current?.selection();
    const before = chats[id] ?? [];
    const messages: Msg[] = [...before.map(({ role, content }) => ({ role, content })), { role: "user", content: text }];
    let mine: Turn[] = [...before, { role: "user", content: text }, { role: "assistant", content: "" }];
    const update = (fn: (last: Turn) => Turn) => {
      mine = [...mine.slice(0, -1), fn(mine[mine.length - 1])];
      setChats((c) => ({ ...c, [id]: mine }));
    };
    setChats((c) => ({ ...c, [id]: mine }));
    setBusy(id);
    abort.current = new AbortController();
    try {
      const body = JSON.stringify({ messages, selection });
      for await (const e of stream(`/api/desk/${id}/chat`, body, abort.current.signal, { "content-type": "application/json" })) {
        if (e.type === "edit") {
          const f = await api.deskFile(id);
          if (shownId.current === id) { show(f); if (e.slide) deck.current?.go(e.slide); }
        }
        if (e.type === "draft" && shownId.current === id) {
          let d = draft.current;
          if (d?.key !== e.key) {       // a new edit starts; one written just before it in the same reply stays on the page
            const base = blocks(d ? d.md : docText.current);
            const start = Math.min(Math.max(e.start, 1), base.length + 1);
            d = draft.current = { key: e.key, base, start, end: Math.min(Math.max(e.end, start - 1), base.length), text: "", md: "" };
          }
          d.text += e.text;
          const words = blocks(d.text).map((b) => b.replace(/^\[(\d+(\]\s?|$)|$)/, ""));   // the model may copy the [n] labels
          d.md = [...d.base.slice(0, d.start - 1), ...words, ...d.base.slice(d.end)].join("\n\n");
          setTyping({ md: d.md, from: d.start - 1, tail: d.base.length - d.end });
        }
        if (e.type === "sources") update((l) => ({ ...l, sources: e.sources }));
        if (e.type === "token") update((l) => ({ ...l, content: l.content + e.text }));
        if (e.type === "error") update((l) => ({ ...l, error: e.message }));
        if (e.type === "step") update((l) => ({ ...l, steps: [...(l.steps ?? []), { tool: e.tool, detail: e.detail }] }));
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") update((l) => ({ ...l, error: String(err) }));
    } finally {
      setBusy(null);
      refresh();
      if (draft.current && shownId.current === id) api.deskFile(id).then(show).catch(() => {});   // words of an edit that was never made
    }
  };

  if (!file) {
    const action = "flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-200 bg-white text-[15px] hover:border-accent hover:text-accent transition";
    return (
      <div className={`h-full overflow-auto ${dragging ? "bg-accent-soft" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); importFile(e.dataTransfer.files[0]); }}>
        <div className="max-w-3xl mx-auto px-8 py-8">
          <h1 className="text-2xl font-semibold tracking-tight">{t("desk_title")}</h1>
          <p className="text-slate-500 mt-1">{t("desk_subtitle")}</p>
          <div className="mt-6 flex flex-wrap gap-3">
            {(["doc", "sheet", "slides"] as const).map((kind) => (
              <button key={kind} onClick={() => create(kind)} className={action}><KindIcon kind={kind} size={18} /> {t(`desk_new_${kind}`)}</button>
            ))}
            <button onClick={() => picker.current?.click()} className={action}><FolderOpen size={18} className="text-slate-500" /> {t("desk_open_file")}</button>
            <input ref={picker} type="file" accept={OPENS} hidden onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
          <p className="mt-3 text-sm text-slate-500">{t("desk_open_hint")}</p>
          {error && <p className="mt-3 flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}

          {!files.length && <p className="mt-10 text-slate-400">{t("desk_empty")}</p>}
          <ul className="mt-8 space-y-2">
            {files.map((f) => (
              <li key={f.id} className="group flex items-center bg-white rounded-xl border border-slate-200 hover:border-accent transition">
                <button onClick={() => openFile(f.id)} className="flex-1 min-w-0 flex items-center gap-3 px-4 py-3 text-left">
                  <KindIcon kind={f.kind} />
                  <span className="min-w-0">
                    <span className="block font-medium truncate">{f.name}</span>
                    <span className="block text-xs text-slate-400">{t(`desk_kind_${f.kind}`)} · {when(f.updated_at, lang)}</span>
                  </span>
                  {busy === f.id && <Loader2 size={15} className="ml-auto animate-spin text-accent" />}
                </button>
                <button onClick={() => remove(f)} title={t("delete")} disabled={busy === f.id}
                  className="p-2 mr-2 rounded-md text-slate-400 hover:text-red-600 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:hidden">
                  <Trash2 size={16} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  }

  const working = busy === file.id;
  return (
    <div className="h-full flex">
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-200 bg-white">
          <button onClick={() => openFile(null)} title={t("desk_back")} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100"><ArrowLeft size={18} /></button>
          <KindIcon kind={file.kind} />
          <input key={file.id + file.name} defaultValue={file.name} onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            className="min-w-0 flex-1 max-w-md px-2 py-1 rounded-md font-semibold text-[17px] bg-transparent outline-none hover:bg-slate-100 focus:bg-slate-100" />
          <span className={`text-xs whitespace-nowrap ${saving === "error" ? "text-red-600" : "text-slate-400"}`}>
            {t(saving === "saved" ? "desk_saved" : saving === "saving" ? "desk_saving" : "desk_save_error")}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            {KINDS[file.kind].formats.map(([format, label]) => (
              <button key={format} onClick={() => download(format)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-slate-50">
                <Download size={14} /> {label}
              </button>
            ))}
            <button onClick={() => setPanel(!panel)} title={t("desk_assistant")}
              className={`p-2 rounded-lg ${panel ? "bg-accent-soft text-accent" : "text-slate-500 hover:bg-slate-100"}`}><PanelRight size={18} /></button>
          </div>
        </header>
        {error && <p className="px-5 py-2 flex gap-2 text-sm text-red-700 bg-red-50"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}
        {file.can_undo && !working && (
          <div className="px-5 py-2 flex items-center gap-3 text-sm bg-amber-50 text-amber-900 border-b border-amber-100">
            <Sparkles size={15} className="shrink-0" /> {t("desk_ai_changed")}
            <button onClick={undoAssistant} className="flex items-center gap-1 font-medium underline underline-offset-2 hover:text-amber-700"><Undo2 size={14} /> {t("desk_undo_ai")}</button>
          </div>
        )}
        {file.kind === "doc" ? (
          <DocEditor key={file.id} ref={page} content={file.content} rev={rev} locked={working} typing={typing}
            onChange={(md) => { if (file.can_undo) setFile({ ...file, can_undo: false }); changed(file.id, md); }} />
        ) : file.kind === "sheet" ? (
          <SheetEditor key={file.id} ref={grid} rows={file.content} values={file.values} locked={working} onShift={shift}
            onChange={(rows) => { setFile({ ...file, can_undo: false, content: rows, values: provisional(rows, file.content, file.values) }); changed(file.id, rows, true); }} />
        ) : (
          <SlidesEditor key={file.id} ref={deck} slides={file.content} rev={rev} locked={working}
            onChange={(slides) => { setFile({ ...file, can_undo: false, content: slides }); changed(file.id, slides); }} />
        )}
      </div>
      {panel && (
        <Assistant turns={chats[file.id] ?? []} kind={file.kind} working={working} busy={busy !== null} docs={docs}
          onSend={send} onStop={() => abort.current?.abort()} onSource={onSource} />
      )}
    </div>
  );
}
