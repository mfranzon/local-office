import { useEffect, useState } from "react";
import { AlertCircle, Download, FileText, Loader2, ShieldCheck } from "lucide-react";
import { api, stream, type SetupStatus } from "../api";
import { useI18n } from "../i18n";

const fmtSize = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);

/** First run: downloads the default models, with one overall progress bar. */
export default function Setup({ onDone }: { onDone: () => void }) {
  const { t, lang, setLang } = useI18n();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [phase, setPhase] = useState<"idle" | "downloading" | "failed">("idle");
  const [percent, setPercent] = useState(0);
  const [current, setCurrent] = useState("");
  useEffect(() => { api.setup().then(setStatus); }, []);

  const start = async () => {
    setPhase("downloading");
    let failed = false;
    try {
      for await (const e of stream("/api/setup/download", "", new AbortController().signal)) {
        if (e.type === "progress") { setPercent(e.percent ?? 0); if (e.model) setCurrent(e.model); }
        if (e.type === "error") failed = true;
      }
    } catch { failed = true; }
    const s = await api.setup();
    if (!failed && s.missing.length === 0) onDone();
    else { setStatus(s); setPhase("failed"); }
  };

  return (
    <div className="h-full grid place-items-center p-8">
      <div className="w-full max-w-lg bg-white rounded-3xl border border-slate-200 shadow-sm p-10">
        <div className="flex items-center justify-between">
          <div className="w-12 h-12 rounded-2xl bg-accent grid place-items-center text-white"><FileText size={24} /></div>
          <div className="flex rounded-md border border-slate-200 overflow-hidden text-xs">
            {(["it", "en"] as const).map((l) => (
              <button key={l} onClick={() => setLang(l)} className={`px-2.5 py-1 uppercase ${lang === l ? "bg-slate-800 text-white" : "hover:bg-slate-100"}`}>{l}</button>
            ))}
          </div>
        </div>
        <h1 className="mt-6 text-2xl font-semibold tracking-tight">{t("setup_title")}</h1>
        <p className="mt-3 text-slate-600 leading-relaxed">{t("setup_text")}</p>

        {status && (
          <ul className="mt-6 space-y-1.5 text-sm text-slate-600">
            {status.missing.map((m) => (
              <li key={m.role} className="flex justify-between"><span>{t(`role_${m.role}` as never)} · {m.name}</span><span className="text-slate-400">{fmtSize(m.size_mb)}</span></li>
            ))}
          </ul>
        )}
        {status && status.chip && <p className="mt-4 text-sm text-slate-500">{t("setup_for_mac", { chip: status.chip, n: status.ram_gb })}</p>}
        {status && status.total_mb / 1000 > status.disk_free_gb && (
          <p className="mt-4 flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {t("setup_no_disk", { need: fmtSize(status.total_mb), free: status.disk_free_gb })}</p>
        )}
        {status && status.ram_gb > 0 && status.ram_gb < 16 && (
          <p className="mt-4 flex gap-2 text-sm text-amber-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {t("setup_low_ram", { n: status.ram_gb })}</p>
        )}

        <div className="mt-8">
          {phase === "downloading" ? (
            <div>
              <div className="flex justify-between text-sm text-slate-600 mb-2">
                <span className="flex items-center gap-2"><Loader2 size={15} className="animate-spin text-accent" /> {current || t("setup_downloading")}</span>
                <span className="tabular-nums">{percent}%</span>
              </div>
              <div className="h-2 rounded-full bg-slate-200 overflow-hidden"><div className="h-full bg-accent transition-all duration-500" style={{ width: `${percent}%` }} /></div>
              <p className="mt-3 text-xs text-slate-400">{t("setup_downloading")}</p>
            </div>
          ) : (
            <>
              {phase === "failed" && <p className="mb-3 text-sm text-red-700">{t("setup_failed")}</p>}
              <button onClick={start} disabled={!status}
                className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-accent text-white font-medium disabled:opacity-40">
                <Download size={18} /> {phase === "failed" ? t("setup_retry") : t("setup_start")}
                {status && <span className="opacity-75 font-normal">· {fmtSize(status.total_mb)}</span>}
              </button>
            </>
          )}
        </div>
        <p className="mt-6 flex items-center gap-2 text-xs text-slate-500"><ShieldCheck size={14} className="text-emerald-600" /> {t("privacy")}</p>
      </div>
    </div>
  );
}
