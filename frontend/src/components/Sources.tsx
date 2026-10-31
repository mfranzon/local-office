import { useCallback, useEffect, useState } from "react";
import { AlertCircle, FolderOpen, Loader2, RefreshCw, X } from "lucide-react";
import { api, type SourceInfo } from "../api";
import { useI18n } from "../i18n";

function since(ts: string, lang: string) {
  const secs = (new Date(ts.replace(" ", "T") + "Z").getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (secs > -60) return rtf.format(0, "minute");
  if (secs > -3600) return rtf.format(Math.round(secs / 60), "minute");
  if (secs > -86400) return rtf.format(Math.round(secs / 3600), "hour");
  return rtf.format(Math.round(secs / 86400), "day");
}

export default function Sources({ onChange }: { onChange: () => void }) {
  const { t, lang } = useI18n();
  const [list, setList] = useState<SourceInfo[]>([]);
  const [canPick, setCanPick] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => api.sources().then((r) => { setList(r.sources); setCanPick(r.can_pick_folder); }).catch(() => {}), []);
  const syncing = list.some((s) => s.status === "syncing");
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, syncing ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [refresh, syncing]);

  const added = () => { refresh(); onChange(); };
  const addFolder = async () => {
    setError(null);
    try {
      const path = canPick ? (await api.pickFolder()).path : prompt(t("folder_path_prompt"));
      if (path) { await api.addFolder(path); added(); }
    } catch (e) { setError((e as Error).message); }
  };
  const remove = async (s: SourceInfo) => {
    if (!confirm(t("confirm_remove_source", { name: s.name }))) return;
    await api.removeSource(s.id); added();
  };

  return (
    <section className="mt-8">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="font-semibold">{t("sources_title")}</h2>
        <button onClick={addFolder} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm hover:bg-slate-50">
          <FolderOpen size={15} /> {t("add_folder")}
        </button>
      </div>
      <p className="text-sm text-slate-500 mb-3">{t("sources_hint")}</p>
      {error && <p className="mb-3 flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}
      {list.length > 0 && (
        <ul className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100">
          {list.map((s) => {
            return (
              <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                <div className="w-9 h-9 rounded-lg bg-accent-soft text-accent grid place-items-center shrink-0"><FolderOpen size={17} /></div>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{s.name}</div>
                  <div className="text-xs text-slate-500 truncate" title={s.config.path}>
                    {s.config.path} · {t("src_docs", { n: s.doc_count })}
                  </div>
                  {s.status === "error" && <div className="text-xs text-red-600 mt-0.5">{s.error}</div>}
                </div>
                <span className="text-xs text-slate-500 shrink-0">
                  {s.status === "syncing"
                    ? <span className="flex items-center gap-1.5 text-accent"><Loader2 size={13} className="animate-spin" /> {t("src_syncing")}</span>
                    : s.last_sync ? t("src_synced", { when: since(s.last_sync, lang) }) : t("src_never")}
                </span>
                <button onClick={() => api.syncSource(s.id).then(() => setTimeout(refresh, 300))} disabled={s.status === "syncing"} title={t("sync_now")}
                  className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-30"><RefreshCw size={16} /></button>
                <button onClick={() => remove(s)} title={t("remove_source")} className="p-2 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-600"><X size={16} /></button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
