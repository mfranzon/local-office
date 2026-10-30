import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Copy, Download, MessageSquare, ScanText, Type, X } from "lucide-react";
import { api, type Doc, type Page } from "../api";
import { useI18n } from "../i18n";
import type { ViewerTarget } from "../App";
import Markdown from "./Markdown";

export default function DocViewer({ target, onClose, onAsk }: {
  target: ViewerTarget; onClose: () => void; onAsk: (doc: Doc) => void;
}) {
  const { t } = useI18n();
  const [doc, setDoc] = useState<(Doc & { pages: Page[] }) | null>(null);
  const [n, setN] = useState(target.page);
  const [copied, setCopied] = useState(false);

  useEffect(() => { api.document(target.id).then(setDoc); setN(target.page); }, [target]);
  const count = doc?.pages.length ?? 0;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setN((p) => Math.min(p + 1, count));
      if (e.key === "ArrowLeft") setN((p) => Math.max(p - 1, 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, onClose]);

  const page = doc?.pages.find((p) => p.n === n);
  const copy = () => { navigator.clipboard.writeText(page?.text ?? ""); setCopied(true); setTimeout(() => setCopied(false), 1500); };

  return (
    <div className="fixed inset-0 z-40 bg-slate-900/50 backdrop-blur-sm grid place-items-center p-6" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl h-full flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center gap-3 px-5 py-3 border-b border-slate-200">
          <h2 className="font-semibold truncate">{doc?.name}</h2>
          <div className="ml-auto flex items-center gap-2">
            {doc && (
              <button onClick={() => onAsk(doc)} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent text-white text-sm">
                <MessageSquare size={15} /> {t("ask_this")}
              </button>
            )}
            <a href={api.fileUrl(target.id)} className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-slate-50">
              <Download size={15} /> {t("download")}
            </a>
            <button onClick={onClose} title={t("close")} className="p-2 rounded-lg hover:bg-slate-100"><X size={18} /></button>
          </div>
        </header>

        <div className="flex-1 grid grid-cols-2 min-h-0">
          <div className="bg-slate-100 overflow-auto p-4 grid place-items-center">
            {page?.has_image ? (
              <img src={api.pageUrl(target.id, n)} alt="" className="max-w-full shadow-md bg-white" />
            ) : (
              <Type size={48} className="text-slate-300" />
            )}
          </div>
          <div className="flex flex-col min-h-0 border-l border-slate-200">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-slate-100 text-sm">
              {page && (
                <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs ${page.method === "ocr" ? "bg-violet-50 text-violet-700" : "bg-emerald-50 text-emerald-700"}`}>
                  {page.method === "ocr" ? <ScanText size={12} /> : <Type size={12} />}
                  {page.method === "ocr" ? t("method_ocr") : t("method_text")}
                </span>
              )}
              <button onClick={copy} className="ml-auto flex items-center gap-1.5 text-slate-500 hover:text-slate-800">
                <Copy size={14} /> {copied ? t("copied") : t("copy")}
              </button>
            </div>
            {page?.method === "ocr" && page.text.trim() ? (
              <div className="flex-1 overflow-auto p-5 text-[14px]"><Markdown text={page.text} /></div>
            ) : (
              <pre className="flex-1 overflow-auto p-5 whitespace-pre-wrap font-sans text-[14px] leading-relaxed">
                {page ? page.text.trim() || t("no_text") : ""}
              </pre>
            )}
          </div>
        </div>

        {count > 1 && (
          <footer className="flex items-center justify-center gap-4 py-2.5 border-t border-slate-200 text-sm">
            <button disabled={n <= 1} onClick={() => setN(n - 1)} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30"><ChevronLeft size={18} /></button>
            <span>{t("page_n", { n })} / {count}</span>
            <button disabled={n >= count} onClick={() => setN(n + 1)} className="p-1.5 rounded-lg hover:bg-slate-100 disabled:opacity-30"><ChevronRight size={18} /></button>
          </footer>
        )}
      </div>
    </div>
  );
}
