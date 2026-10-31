import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Cpu, Download, Loader2, ScanText, Search, Sparkles } from "lucide-react";
import { api, stream, type ModelSettings, type ModelSpec, type Role } from "../api";
import { useI18n } from "../i18n";

const ROLES = ["assistant", "ocr", "embed"] as const;
const ROLE_ICONS = { assistant: Cpu, ocr: ScanText, embed: Search } as const;
const STEPS = ["pull", "configure", "test", "reindex"] as const;
type Step = (typeof STEPS)[number];

interface Run { role: Role; step: Step; percent?: number; done?: string; error?: string }

const norm = (ref: string) => ref.trim().toLowerCase().replace("huggingface.co/", "hf.co/");
const fmtSize = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);

function RoleCard({ role, data, ramGb, diskGb, run, busy, onSwitch }: {
  role: Role; data: ModelSettings[Role]; ramGb: number; diskGb: number; run: Run | null; busy: boolean; onSwitch: (ref: string) => void;
}) {
  const { t } = useI18n();
  const activeRef = norm(data.active.ref);
  const inCatalog = data.choices.some((c) => norm(c.ref) === activeRef);
  const [selected, setSelected] = useState(inCatalog ? activeRef : "custom");
  const [custom, setCustom] = useState(inCatalog ? "" : data.active.ref);
  useEffect(() => { if (run?.done) setSelected(activeRef); }, [run?.done, activeRef]);

  const target = selected === "custom" ? custom.trim() : selected;
  const changed = !!target && norm(target) !== activeRef;
  const Icon = ROLE_ICONS[role];
  const running = run && !run.done && !run.error;
  const steps = STEPS.filter((s) => s !== "reindex" || role === "embed");

  const Option = ({ spec }: { spec: ModelSpec }) => {
    const ref = norm(spec.ref);
    return (
      <label className={`flex items-center gap-3 px-4 py-3 cursor-pointer ${selected === ref ? "bg-accent-soft/50" : "hover:bg-slate-50"}`}>
        <input type="radio" name={role} checked={selected === ref} onChange={() => setSelected(ref)} disabled={!!running} className="accent-[#2f5bd3] w-4 h-4" />
        <span className="font-medium">{spec.name}</span>
        {spec.tags.map((tag) => (
          <span key={tag} className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{t(`tag_${tag}` as never)}</span>
        ))}
        {ref === data.recommended && (
          <span className="flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 font-medium"><Sparkles size={11} /> {t("recommended")}</span>
        )}
        {ramGb > 0 && spec.min_ram_gb > ramGb && (
          <span className="flex items-center gap-1 text-[11px] text-amber-700"><AlertCircle size={12} /> {t("needs_ram", { n: spec.min_ram_gb })}</span>
        )}
        {!spec.downloaded && spec.size_mb / 1000 > diskGb && (
          <span className="flex items-center gap-1 text-[11px] text-red-700"><AlertCircle size={12} /> {t("no_disk")}</span>
        )}
        <span className="ml-auto text-xs">
          {ref === activeRef ? (
            <span className="flex items-center gap-1 text-emerald-700 font-medium"><CheckCircle2 size={14} /> {t("in_use")}</span>
          ) : spec.downloaded ? (
            <span className="text-slate-500">{t("downloaded")}</span>
          ) : (
            <span className="flex items-center gap-1 text-slate-400"><Download size={13} /> {t("to_download", { size: fmtSize(spec.size_mb) })}</span>
          )}
        </span>
      </label>
    );
  };

  return (
    <section className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
      <header className="flex gap-3 px-5 pt-4 pb-3">
        <div className="w-9 h-9 rounded-lg bg-accent-soft text-accent grid place-items-center shrink-0"><Icon size={18} /></div>
        <div>
          <h2 className="font-semibold">{t(`role_${role}` as never)}</h2>
          <p className="text-sm text-slate-500">{t(`role_${role}_d` as never)}</p>
        </div>
      </header>

      <div className="divide-y divide-slate-100 border-t border-slate-100">
        {data.choices.map((spec) => <Option key={spec.ref} spec={spec} />)}
        <div className={`px-4 py-3 ${selected === "custom" ? "bg-accent-soft/50" : ""}`}>
          <label className="flex items-center gap-3 cursor-pointer">
            <input type="radio" name={role} checked={selected === "custom"} onChange={() => setSelected("custom")} disabled={!!running} className="accent-[#2f5bd3] w-4 h-4" />
            <span className="font-medium">{t("custom_model")}</span>
            {!inCatalog && <span className="ml-auto flex items-center gap-1 text-xs text-emerald-700 font-medium"><CheckCircle2 size={14} /> {t("in_use")}</span>}
          </label>
          {selected === "custom" && (
            <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder={t("custom_hint")} disabled={!!running}
              className="mt-2 ml-7 w-[calc(100%-1.75rem)] px-3 py-2 rounded-lg border border-slate-200 text-sm font-mono outline-none focus:border-accent" />
          )}
        </div>
      </div>

      {(changed || run) && (
        <footer className="px-5 py-4 border-t border-slate-100 bg-slate-50/60">
          {running ? (
            <ol className="space-y-2">
              {steps.map((s) => {
                const idx = steps.indexOf(s), cur = steps.indexOf(run.step);
                return (
                  <li key={s} className={`flex items-center gap-2 text-sm ${idx > cur ? "text-slate-400" : ""}`}>
                    {idx < cur ? <CheckCircle2 size={15} className="text-emerald-600" /> : idx === cur ? <Loader2 size={15} className="animate-spin text-accent" /> : <span className="w-[15px]" />}
                    {t(`step_${s}` as never)}
                    {idx === cur && run.percent !== undefined && (s === "pull" || s === "reindex") && (
                      <>
                        <div className="w-40 h-1.5 rounded-full bg-slate-200 overflow-hidden ml-2">
                          <div className="h-full bg-accent transition-all" style={{ width: `${run.percent}%` }} />
                        </div>
                        <span className="text-xs text-slate-500 tabular-nums">{run.percent}%</span>
                      </>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : (
            <div className="flex items-center gap-3">
              {run?.done && !changed && <span className="flex items-center gap-1.5 text-sm text-emerald-700"><CheckCircle2 size={16} /> {t("switched", { name: run.done })}</span>}
              {run?.error && (
                <span className="flex items-start gap-1.5 text-sm text-red-700">
                  <AlertCircle size={16} className="shrink-0 mt-0.5" />
                  <span>{t("switch_failed")}<span className="block text-xs text-red-600/80 mt-0.5 font-mono">{run.error}</span></span>
                </span>
              )}
              {changed && (
                <button onClick={() => onSwitch(target)} disabled={busy}
                  title={busy ? t("busy_other") : undefined}
                  className="ml-auto shrink-0 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium disabled:opacity-40">{t("use_model")}</button>
              )}
            </div>
          )}
        </footer>
      )}
    </section>
  );
}

export default function Settings() {
  const { t } = useI18n();
  const [data, setData] = useState<ModelSettings | null>(null);
  const [runs, setRuns] = useState<Partial<Record<Role, Run>>>({});
  const refresh = () => api.modelSettings().then(setData).catch(() => {});
  useEffect(() => { refresh(); }, []);

  const busy = !!data?.busy || Object.values(runs).some((r) => r && !r.done && !r.error);

  const switchModel = async (role: Role, ref: string) => {
    const set = (r: Partial<Run>) => setRuns((all) => ({ ...all, [role]: { ...all[role]!, ...r } }));
    setRuns((all) => ({ ...all, [role]: { role, step: "pull", percent: 0 } }));
    try {
      const body = JSON.stringify({ role, ref });
      for await (const e of stream("/api/settings/models", body, new AbortController().signal, { "content-type": "application/json" })) {
        if (e.type === "progress") set({ step: e.step, percent: e.percent });
        if (e.type === "switched") set({ done: e.model.name || e.model.ref });
        if (e.type === "error") set({ error: e.message });
      }
    } catch (err) {
      set({ error: String(err) });
    }
    refresh();
  };

  const differs = data ? ROLES.filter((r) => norm(data[r].active.ref) !== data[r].recommended) : [];
  const useRecommended = async () => {
    for (const role of differs) await switchModel(role, data![role].recommended);   // one at a time: each is downloaded and tested
  };

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-3xl mx-auto px-8 py-8">
        <h1 className="text-2xl font-semibold tracking-tight">{t("set_title")}</h1>
        <p className="text-slate-500 mt-1">{t("set_subtitle")}</p>
        {data && data.ram_gb > 0 && (
          <p className="mt-4 flex items-center gap-2 text-sm text-slate-600"><Cpu size={15} className="text-slate-400" />
            {t("this_mac", { chip: data.chip || "Mac", n: data.ram_gb, disk: data.disk_free_gb })}</p>
        )}
        {data && differs.length > 0 && (
          <div className="mt-4 flex items-center gap-3 px-4 py-3 rounded-xl border border-emerald-200 bg-emerald-50/60 text-sm text-emerald-900">
            <Sparkles size={16} className="shrink-0 text-emerald-600" /> {t("reco_differs")}
            <button onClick={useRecommended} disabled={busy}
              className="ml-auto shrink-0 px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium disabled:opacity-40">{t("use_recommended")}</button>
          </div>
        )}
        <div className="mt-6 space-y-5">
          {data && ROLES.map((role) => (
            <RoleCard key={role} role={role} data={data[role]} ramGb={data.ram_gb} diskGb={data.disk_free_gb} run={runs[role] ?? null} busy={busy}
              onSwitch={(ref) => switchModel(role, ref)} />
          ))}
          {!data && <Loader2 className="animate-spin text-slate-400 mx-auto" />}
        </div>
      </div>
    </div>
  );
}
