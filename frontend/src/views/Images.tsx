import { useEffect, useRef, useState } from "react";
import { BarChart3, Check, FilePlus2, ImagePlus, Loader2, ScanText, Sparkles, Table } from "lucide-react";
import { api, stream, type Msg } from "../api";
import { useI18n } from "../i18n";
import Composer from "../components/Composer";
import Markdown from "../components/Markdown";

export default function Images({ refreshDocs }: { refreshDocs: () => void }) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [turns, setTurns] = useState<(Msg & { error?: string })[]>([]);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const pick = (f: File) => {
    abort.current?.abort();
    setFile(f); setTurns([]); setAdded(false);
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(f); });
  };

  useEffect(() => {
    const paste = (e: ClipboardEvent) => { const f = e.clipboardData?.files[0]; if (f?.type.startsWith("image/")) pick(f); };
    const over = (e: DragEvent) => e.preventDefault();
    const drop = (e: DragEvent) => { e.preventDefault(); const f = e.dataTransfer?.files[0]; if (f) pick(f); };
    window.addEventListener("paste", paste); window.addEventListener("dragover", over); window.addEventListener("drop", drop);
    return () => { window.removeEventListener("paste", paste); window.removeEventListener("dragover", over); window.removeEventListener("drop", drop); };
  }, []);

  const send = async (question: string) => {
    if (!file) return;
    const history: Msg[] = [...turns.map(({ role, content }) => ({ role, content })), { role: "user", content: question }];
    setTurns([...history, { role: "assistant", content: "" }]);
    setBusy(true);
    abort.current = new AbortController();
    const form = new FormData();
    form.append("image", file);
    form.append("messages", JSON.stringify(history));
    const update = (fn: (c: Msg & { error?: string }) => Msg & { error?: string }) =>
      setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);
    try {
      for await (const e of stream("/api/vision", form, abort.current.signal)) {
        if (e.type === "token") update((l) => ({ ...l, content: l.content + e.text }));
        if (e.type === "error") update((l) => ({ ...l, error: e.message }));
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") update((l) => ({ ...l, error: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const addToDocs = async () => {
    if (!file) return;
    await api.upload([file]); setAdded(true); refreshDocs();
  };

  const actions = [
    [t("qa_describe"), t("p_describe"), Sparkles],
    [t("qa_text"), t("p_text"), ScanText],
    [t("qa_table"), t("p_table"), Table],
    [t("qa_chart"), t("p_chart"), BarChart3],
  ] as const;

  const chooser = (
    <input ref={input} type="file" accept="image/*,.heic,.heif" hidden
      onChange={(e) => { const f = e.target.files?.[0]; if (f) pick(f); e.target.value = ""; }} />
  );

  if (!file)
    return (
      <div className="h-full overflow-auto">
        <div className="max-w-3xl mx-auto px-8 py-8">
          <h1 className="text-2xl font-semibold tracking-tight">{t("img_title")}</h1>
          <p className="text-slate-500 mt-1">{t("img_subtitle")}</p>
          <button onClick={() => input.current?.click()}
            className="mt-6 w-full rounded-2xl border-2 border-dashed border-slate-300 bg-white hover:border-accent hover:bg-accent-soft/40 transition px-6 py-16 text-center">
            <div className="w-12 h-12 mx-auto rounded-full bg-accent-soft text-accent grid place-items-center"><ImagePlus size={22} /></div>
            <div className="mt-3 text-lg font-medium">{t("img_drop")}</div>
          </button>
          {chooser}
        </div>
      </div>
    );

  return (
    <div className="h-full grid grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="bg-slate-100 border-r border-slate-200 flex flex-col min-h-0">
        <div className="flex-1 overflow-auto p-6 grid place-items-center">
          <img src={preview!} alt="" className="max-w-full max-h-full rounded-lg shadow-md bg-white" />
        </div>
        <div className="flex gap-2 p-4 border-t border-slate-200 bg-white">
          <button onClick={() => input.current?.click()} className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-slate-50">{t("img_change")}</button>
          <button onClick={addToDocs} disabled={added}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-slate-50 disabled:text-emerald-700">
            {added ? <><Check size={15} /> {t("img_added")}</> : <><FilePlus2 size={15} /> {t("img_add_docs")}</>}
          </button>
          {chooser}
        </div>
      </div>

      <div className="flex flex-col min-h-0">
        <div className="flex-1 overflow-auto px-6 py-6 space-y-5">
          <div className="flex flex-wrap gap-2">
            {actions.map(([label, prompt, Icon]) => (
              <button key={label} onClick={() => send(prompt)} disabled={busy}
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-sm hover:border-accent hover:text-accent disabled:opacity-40">
                <Icon size={16} /> {label}
              </button>
            ))}
          </div>
          {turns.map((turn, i) => turn.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] px-4 py-2.5 rounded-2xl rounded-br-md bg-accent text-white">{turn.content}</div>
            </div>
          ) : (
            <div key={i} className="text-[15px]">
              {!turn.content && busy && i === turns.length - 1 && <Loader2 size={16} className="animate-spin text-slate-400" />}
              <Markdown text={turn.content} />
              {turn.error && <div className="mt-2 px-3 py-2 rounded-lg bg-red-50 text-red-700 text-sm">{t("error")} {turn.error}</div>}
            </div>
          ))}
        </div>
        <div className="p-4">
          <Composer placeholder={t("img_placeholder")} busy={busy} onSend={send} onStop={() => abort.current?.abort()} />
        </div>
      </div>
    </div>
  );
}
