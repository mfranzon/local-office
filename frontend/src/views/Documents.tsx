import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileSpreadsheet, FileText, Loader2, MessageSquare, RotateCcw, Search, Trash2, Upload } from "lucide-react";
import { api, type Doc } from "../api";
import { useI18n } from "../i18n";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.heic,.heif,.tif,.tiff,.webp,.bmp,.docx,.xlsx,.txt,.md,.csv";

function useFileDrop(onFiles: (files: File[]) => void) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  useEffect(() => {
    const hasFiles = (e: DragEvent) => e.dataTransfer?.types.includes("Files");
    const enter = (e: DragEvent) => { if (hasFiles(e)) { depth.current++; setDragging(true); } };
    const leave = () => { if (--depth.current <= 0) { depth.current = 0; setDragging(false); } };
    const over = (e: DragEvent) => { if (hasFiles(e)) e.preventDefault(); };
    const drop = (e: DragEvent) => {
      e.preventDefault(); depth.current = 0; setDragging(false);
      if (e.dataTransfer?.files.length) onFiles([...e.dataTransfer.files]);
    };
    const paste = (e: ClipboardEvent) => {
      const files = [...(e.clipboardData?.files ?? [])];
      if (!files.length) return;
      const stamp = new Date().toISOString().slice(0, 16).replace("T", " ").replace(":", "-");
      onFiles(files.map((f, i) => new File([f], `${stamp}${i ? `-${i}` : ""}.${f.type.split("/")[1] || "png"}`, { type: f.type })));
    };
    window.addEventListener("dragenter", enter); window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over); window.addEventListener("drop", drop);
    window.addEventListener("paste", paste);
    return () => {
      window.removeEventListener("dragenter", enter); window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over); window.removeEventListener("drop", drop);
      window.removeEventListener("paste", paste);
    };
  }, [onFiles]);
  return dragging;
}

function Thumb({ doc }: { doc: Doc }) {
  const [failed, setFailed] = useState(false);
  const Icon = doc.ext === ".xlsx" || doc.ext === ".csv" ? FileSpreadsheet : FileText;
  if (doc.pages_done > 0 && !failed)
    return <img src={api.pageUrl(doc.id, 1)} onError={() => setFailed(true)} alt="" className="w-11 h-14 object-cover object-top rounded border border-slate-200 bg-white" />;
  return <div className="w-11 h-14 rounded border border-slate-200 bg-slate-50 grid place-items-center text-slate-400"><Icon size={20} /></div>;
}

function StatusCell({ doc, onRetry }: { doc: Doc; onRetry: () => void }) {
  const { t } = useI18n();
  if (doc.status === "ready")
    return <span className="flex items-center gap-1.5 text-emerald-700 text-sm"><CheckCircle2 size={16} /> {t("st_ready")}</span>;
  if (doc.status === "error")
    return (
      <span className="flex items-center gap-2 text-sm">
        <span className="flex items-center gap-1.5 text-red-600" title={doc.error ?? ""}><AlertCircle size={16} /> {t("st_error")}</span>
        <button onClick={onRetry} className="flex items-center gap-1 text-accent hover:underline"><RotateCcw size={13} /> {t("retry")}</button>
      </span>
    );
  if (doc.status === "queued") return <span className="text-sm text-slate-500">{t("st_queued")}</span>;
  const pct = doc.pages_total ? Math.round((doc.pages_done / doc.pages_total) * 100) : 0;
  return (
    <div className="w-48">
      <div className="flex items-center gap-1.5 text-sm text-slate-600 mb-1">
        <Loader2 size={14} className="animate-spin text-accent" />
        {doc.pages_total ? t("st_reading", { n: Math.min(doc.pages_done + 1, doc.pages_total), t: doc.pages_total }) : t("st_preparing")}
      </div>
      <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
        <div className="h-full bg-accent transition-all duration-700" style={{ width: `${Math.max(pct, 4)}%` }} />
      </div>
    </div>
  );
}

export default function Documents({ docs, refresh, onOpen, onAsk }: {
  docs: Doc[]; refresh: () => void; onOpen: (id: string) => void; onAsk: (doc: Doc) => void;
}) {
  const { t, lang } = useI18n();
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const onFiles = useCallback(async (files: File[]) => {
    const res = await api.upload(files);
    if (res.rejected.length) { setNotice(res.rejected.join(", ")); setTimeout(() => setNotice(null), 6000); }
    refresh();
  }, [refresh]);
  const dragging = useFileDrop(onFiles);

  const remove = async (doc: Doc) => {
    if (!confirm(t("confirm_delete", { name: doc.name }))) return;
    await api.remove(doc.id); refresh();
  };
  const shown = docs.filter((d) => d.name.toLowerCase().includes(filter.toLowerCase()));
  const fmtDate = (s: string) => new Date(s.replace(" ", "T") + "Z").toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-5xl mx-auto px-8 py-8">
        <h1 className="text-2xl font-semibold tracking-tight">{t("docs_title")}</h1>
        <p className="text-slate-500 mt-1">{t("docs_subtitle")}</p>

        <div onClick={() => input.current?.click()}
          className="mt-6 cursor-pointer rounded-2xl border-2 border-dashed border-slate-300 bg-white hover:border-accent hover:bg-accent-soft/40 transition px-6 py-10 text-center">
          <div className="w-12 h-12 mx-auto rounded-full bg-accent-soft text-accent grid place-items-center"><Upload size={22} /></div>
          <div className="mt-3 text-lg font-medium">{t("drop_title")}</div>
          <div className="text-slate-500 mt-1">
            {t("drop_or")} <span className="text-accent font-medium">{t("drop_choose")}</span>
          </div>
          <div className="text-xs text-slate-400 mt-3">{t("drop_hint")}</div>
          <input ref={input} type="file" multiple accept={ACCEPT} hidden
            onChange={(e) => { if (e.target.files?.length) onFiles([...e.target.files]); e.target.value = ""; }} />
        </div>

        {notice && <div className="mt-4 px-4 py-2.5 rounded-lg bg-amber-50 text-amber-800 text-sm">{t("rejected")} {notice}</div>}

        {docs.length > 0 && (
          <div className="mt-8">
            <div className="relative mb-3 w-72">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("filter")}
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-200 bg-white text-sm outline-none focus:border-accent" />
            </div>
            <ul className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100">
              {shown.map((doc) => (
                <li key={doc.id} className="flex items-center gap-4 px-4 py-3 group">
                  <button onClick={() => onOpen(doc.id)} className="flex items-center gap-4 min-w-0 flex-1 text-left">
                    <Thumb doc={doc} />
                    <div className="min-w-0">
                      <div className="font-medium truncate group-hover:text-accent">{doc.name}</div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {fmtDate(doc.created_at)}
                        {doc.pages_total > 0 && <> · {doc.pages_total === 1 ? t("page_one") : t("pages", { n: doc.pages_total })}</>}
                        {doc.status === "ready" && (doc.wiki_status === "pending" || doc.wiki_status === "processing") && (
                          <span className="ml-2 inline-flex items-center gap-1 text-violet-600"><Loader2 size={11} className="animate-spin" /> {t("wiki_updating")}</span>
                        )}
                      </div>
                    </div>
                  </button>
                  <StatusCell doc={doc} onRetry={() => api.retry(doc.id).then(refresh)} />
                  <div className="flex items-center gap-1 w-20 justify-end">
                    {doc.status === "ready" && (
                      <button onClick={() => onAsk(doc)} title={t("ask_this")} className="p-2 rounded-lg text-slate-500 hover:bg-accent-soft hover:text-accent"><MessageSquare size={17} /></button>
                    )}
                    <button onClick={() => remove(doc)} title={t("delete")} className="p-2 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-600"><Trash2 size={17} /></button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
        {docs.length === 0 && <p className="mt-10 text-center text-slate-400">{t("empty_docs")}</p>}
      </div>

      {dragging && (
        <div className="fixed inset-0 z-50 bg-accent/10 backdrop-blur-[2px] border-4 border-dashed border-accent grid place-items-center pointer-events-none">
          <div className="bg-white rounded-2xl shadow-xl px-8 py-6 flex items-center gap-3 text-lg font-medium text-accent">
            <Upload size={24} /> {t("drop_overlay")}
          </div>
        </div>
      )}
    </div>
  );
}
