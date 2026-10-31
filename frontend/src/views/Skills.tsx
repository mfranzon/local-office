import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, Lock, Pencil, Play, Plus, Trash2, Upload, Wand2 } from "lucide-react";
import { api, type SkillInfo } from "../api";
import { useI18n } from "../i18n";
import Markdown from "../components/Markdown";

interface Draft { name: string; description: string; instructions: string; isNew: boolean }

function Editor({ draft, onCancel, onSaved }: { draft: Draft; onCancel: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const [d, setD] = useState(draft);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    try { await api.saveSkill(d.name.trim(), d.description, d.instructions); onSaved(); }
    catch (e) { setError((e as Error).message); }
  };
  const field = "w-full px-3 py-2 rounded-lg border border-slate-200 text-sm outline-none focus:border-accent";
  return (
    <form onSubmit={(e) => { e.preventDefault(); save(); }} className="bg-white rounded-2xl border border-accent/40 p-5 space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">{t("skill_name")}</label>
        <input required disabled={!d.isNew} value={d.name} placeholder={t("skill_name_hint")}
          onChange={(e) => setD({ ...d, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} className={`${field} font-mono disabled:bg-slate-50`} />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">{t("skill_description")}</label>
        <textarea required rows={2} value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} className={field} />
        <p className="text-xs text-slate-500 mt-1">{t("skill_description_hint")}</p>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">{t("skill_instructions")}</label>
        <textarea rows={10} value={d.instructions} onChange={(e) => setD({ ...d, instructions: e.target.value })} className={`${field} font-mono text-[13px]`} />
        <p className="text-xs text-slate-500 mt-1">{t("skill_instructions_hint")}</p>
      </div>
      {error && <p className="flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="px-4 py-2 rounded-lg text-sm hover:bg-slate-100">{t("cancel")}</button>
        <button className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium">{t("skill_save")}</button>
      </div>
    </form>
  );
}

function SkillCard({ skill, onTry, onEdit, onChanged }: {
  skill: SkillInfo; onTry: () => void; onEdit: () => void; onChanged: () => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const remove = async () => {
    if (!confirm(t("confirm_delete_skill", { name: skill.name }))) return;
    await api.deleteSkill(skill.name); onChanged();
  };
  return (
    <li className={`bg-white rounded-2xl border border-slate-200 ${skill.enabled ? "" : "opacity-60"}`}>
      <div className="flex items-start gap-4 p-4">
        <div className="w-9 h-9 rounded-lg bg-violet-50 text-violet-600 grid place-items-center shrink-0"><Wand2 size={17} /></div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium font-mono text-[15px]">/{skill.name}</span>
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{skill.builtin ? t("skill_builtin") : t("skill_custom")}</span>
          </div>
          <p className="text-sm text-slate-600 mt-1">{skill.description}</p>
          {skill.ignored.length > 0 && <p className="text-xs text-amber-700 mt-1">{t("skill_ignored", { files: skill.ignored.join(", ") })}</p>}
          <div className="flex items-center gap-1 mt-2 -ml-2 text-sm">
            <button onClick={() => setOpen(!open)} className="flex items-center gap-1 px-2 py-1 rounded-md text-slate-500 hover:bg-slate-100">
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {t("skill_show")}
            </button>
            {!skill.builtin && <button onClick={onEdit} className="flex items-center gap-1 px-2 py-1 rounded-md text-slate-500 hover:bg-slate-100"><Pencil size={13} /> {t("skill_edit")}</button>}
            {!skill.builtin && <button onClick={remove} className="flex items-center gap-1 px-2 py-1 rounded-md text-slate-500 hover:bg-red-50 hover:text-red-600"><Trash2 size={13} /> {t("skill_delete")}</button>}
          </div>
        </div>
        <button onClick={onTry} disabled={!skill.enabled}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-accent-soft hover:text-accent disabled:opacity-40">
          <Play size={14} /> {t("skill_try")}
        </button>
        <label className="relative inline-flex items-center cursor-pointer mt-1.5" title={skill.name}>
          <input type="checkbox" checked={skill.enabled} onChange={(e) => api.toggleSkill(skill.name, e.target.checked).then(onChanged)} className="sr-only peer" />
          <span className="w-9 h-5 rounded-full bg-slate-300 peer-checked:bg-accent transition" />
          <span className="absolute left-0.5 top-0.5 w-4 h-4 rounded-full bg-white transition peer-checked:translate-x-4" />
        </label>
      </div>
      {open && <div className="border-t border-slate-100 px-5 py-4 text-sm"><Markdown text={skill.instructions} /></div>}
    </li>
  );
}

export default function Skills({ onTry }: { onTry: (name: string) => void }) {
  const { t } = useI18n();
  const [list, setList] = useState<SkillInfo[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const refresh = useCallback(() => api.skills().then(setList).catch((e) => setError(String(e))), []);
  useEffect(() => { refresh(); }, [refresh]);

  const importFile = async (file: File) => {
    setError(null);
    try { await api.importSkill(file); refresh(); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-3xl mx-auto px-8 py-8">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{t("skills_title")}</h1>
          <button onClick={() => input.current?.click()} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm hover:bg-slate-50">
            <Upload size={15} /> {t("skill_import")}
          </button>
          <button onClick={() => setDraft({ name: "", description: "", instructions: "1. \n2. \n", isNew: true })}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-white text-sm font-medium">
            <Plus size={15} /> {t("skill_new")}
          </button>
          <input ref={input} type="file" accept=".md,.zip" hidden onChange={(e) => { if (e.target.files?.[0]) importFile(e.target.files[0]); e.target.value = ""; }} />
        </div>
        <p className="text-slate-500 mt-1">{t("skills_subtitle")}</p>
        <p className="mt-3 flex gap-2 text-sm text-slate-600"><Lock size={15} className="shrink-0 mt-0.5 text-emerald-600" /> {t("skills_safety")}</p>
        {error && <p className="mt-3 flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}

        <div className="mt-6 space-y-3">
          {draft && <Editor draft={draft} onCancel={() => setDraft(null)} onSaved={() => { setDraft(null); refresh(); }} />}
          <ul className="space-y-3">
            {list?.map((s) => (
              <SkillCard key={s.name} skill={s} onTry={() => onTry(s.name)} onChanged={refresh}
                onEdit={() => setDraft({ name: s.name, description: s.description, instructions: s.instructions, isNew: false })} />
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
