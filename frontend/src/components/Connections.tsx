import { useEffect, useState } from "react";
import { AlertCircle, AlertTriangle, BookOpenText, ChevronDown, ChevronRight, Globe, KeyRound, Loader2, Plug, Plus, RefreshCw, X } from "lucide-react";
import { api, type Connection } from "../api";
import { useI18n } from "../i18n";

/** Public services that need no account, offered with one click. Nothing is connected until the user picks one. */
const PRESETS = [
  { name: "Web search", url: "https://mcp.exa.ai/mcp", icon: Globe, about: "preset_web" },
  { name: "Microsoft Office help", url: "https://learn.microsoft.com/api/mcp", icon: BookOpenText, about: "preset_office" },
] as const;

/** "Authorization: Bearer <token>" unless the user names another header or already wrote the scheme. */
function authHeaders(token: string, header: string): Record<string, string> {
  const name = header.trim() || "Authorization";
  const value = token.trim();
  if (!value) return {};
  return { [name]: name.toLowerCase() === "authorization" && !/^(bearer|basic|token)\s/i.test(value) ? `Bearer ${value}` : value };
}

function Server({ c, onChange }: { c: Connection; onChange: (c: Connection | null) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const on = c.tools.filter((x) => x.enabled).length;

  const act = async (fn: () => Promise<Connection>) => {
    setBusy(true);
    try { onChange(await fn()); } catch (e) { alert((e as Error).message); } finally { setBusy(false); }
  };
  const setToken = () => {
    const token = prompt(t("conn_token_prompt"));
    if (token !== null) act(() => api.editConnection(c.id, { headers: authHeaders(token, c.header_names[0] ?? "") }));
  };
  const remove = async () => {
    if (!confirm(t("conn_confirm_remove", { name: c.name }))) return;
    await api.removeConnection(c.id);
    onChange(null);
  };

  return (
    <li>
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="w-9 h-9 rounded-lg bg-amber-50 text-amber-700 grid place-items-center shrink-0"><Plug size={17} /></div>
        <button onClick={() => setOpen(!open)} className="min-w-0 flex-1 text-left">
          <div className="font-medium truncate flex items-center gap-1.5">
            {open ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}{c.name}
          </div>
          <div className="text-xs text-slate-500 truncate" title={c.url}>
            {c.url} · {c.error ? t("conn_error") : t("conn_tools_on", { n: on, t: c.tools.length })}
          </div>
          {c.error && <div className="text-xs text-red-600 mt-0.5">{c.error}</div>}
        </button>
        <label className="flex items-center gap-1.5 text-xs text-slate-600 shrink-0 cursor-pointer">
          <input type="checkbox" checked={c.enabled} disabled={busy} onChange={(e) => act(() => api.editConnection(c.id, { enabled: e.target.checked }))}
            className="accent-[#2f5bd3] w-4 h-4" />
          {t("conn_on")}
        </label>
        <button onClick={setToken} disabled={busy} title={t("conn_token")} className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-30"><KeyRound size={16} /></button>
        <button onClick={() => act(() => api.refreshConnection(c.id))} disabled={busy} title={t("conn_refresh")}
          className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-30">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
        </button>
        <button onClick={remove} title={t("conn_remove")} className="p-2 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-600"><X size={16} /></button>
      </div>
      {open && c.tools.length > 0 && (
        <ul className="px-4 pb-3 pl-16 space-y-1.5">
          {c.tools.map((tool) => (
            <li key={tool.name}>
              <label className="flex items-start gap-2.5 text-sm cursor-pointer">
                <input type="checkbox" checked={tool.enabled} disabled={busy}
                  onChange={(e) => act(() => api.editConnection(c.id, { tools: { [tool.name]: e.target.checked } }))}
                  className="accent-[#2f5bd3] w-4 h-4 mt-0.5 shrink-0" />
                <span className="min-w-0">
                  <span className="font-medium">{tool.title || tool.name}</span>{" "}
                  {tool.read_only
                    ? <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{t("conn_read_only")}</span>
                    : <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-800">{t("conn_can_change")}</span>}
                  {tool.description && <span className="block text-xs text-slate-500 line-clamp-2">{tool.description}</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function Connections() {
  const { t } = useI18n();
  const [list, setList] = useState<Connection[]>([]);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", url: "", token: "", header: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.connections().then(setList).catch(() => {}); }, []);

  const add = async (preset?: (typeof PRESETS)[number]) => {
    setBusy(true); setError(null);
    try {
      const c = preset ? await api.addConnection(preset.name, preset.url, {})
        : await api.addConnection(form.name, form.url, authHeaders(form.token, form.header));
      setList((l) => [...l, c]);
      setForm({ name: "", url: "", token: "", header: "" });
      setAdding(false);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const field = (key: keyof typeof form, placeholder: string, extra = "") => (
    <input value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} placeholder={placeholder}
      type={key === "token" ? "password" : "text"}
      className={`px-3 py-2 rounded-lg border border-slate-200 text-sm outline-none focus:border-accent ${extra}`} />
  );

  return (
    <section className="mt-10">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-lg font-semibold">{t("conn_title")}</h2>
        {!adding && (
          <button onClick={() => setAdding(true)} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm hover:bg-slate-50">
            <Plus size={15} /> {t("conn_add")}
          </button>
        )}
      </div>
      <p className="text-sm text-slate-500">{t("conn_subtitle")}</p>
      <p className="mt-3 flex gap-2 px-4 py-3 rounded-xl border border-amber-200 bg-amber-50/60 text-sm text-amber-900">
        <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-600" /> {t("conn_warning")}
      </p>

      {adding && (
        <div className="mt-4 p-4 bg-white rounded-2xl border border-slate-200 space-y-2">
          <div className="flex gap-2">
            {field("name", t("conn_name"), "w-40")}
            {field("url", "https://…/mcp", "flex-1 font-mono")}
          </div>
          <div className="flex gap-2">
            {field("token", t("conn_token_hint"), "flex-1")}
            {field("header", "Authorization", "w-40 font-mono")}
          </div>
          {error && <p className="flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}
          <div className="flex gap-2 justify-end pt-1">
            <button onClick={() => { setAdding(false); setError(null); }} className="px-3 py-1.5 rounded-lg text-sm hover:bg-slate-100">{t("cancel")}</button>
            <button onClick={() => add()} disabled={busy || !form.name.trim() || !form.url.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-accent text-white text-sm font-medium disabled:opacity-40">
              {busy && <Loader2 size={14} className="animate-spin" />} {t("conn_connect")}
            </button>
          </div>
        </div>
      )}

      {PRESETS.some((p) => !list.some((c) => c.url === p.url)) && (
        <div className="mt-4 grid grid-cols-2 gap-3">
          {PRESETS.filter((p) => !list.some((c) => c.url === p.url)).map((p) => (
            <div key={p.url} className="flex flex-col gap-2 p-4 bg-white rounded-2xl border border-dashed border-slate-300">
              <div className="flex items-center gap-2 font-medium"><p.icon size={16} className="text-amber-700" /> {p.name}</div>
              <p className="text-xs text-slate-500 flex-1">{t(p.about)}</p>
              <button onClick={() => add(p)} disabled={busy}
                className="self-start flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-slate-50 disabled:opacity-40">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />} {t("conn_connect")}
              </button>
            </div>
          ))}
        </div>
      )}
      {error && !adding && <p className="mt-3 flex gap-2 text-sm text-red-700"><AlertCircle size={16} className="shrink-0 mt-0.5" /> {error}</p>}

      {list.length > 0 && (
        <ul className="mt-4 bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100">
          {list.map((c) => (
            <Server key={c.id} c={c} onChange={(next) => setList((l) => next ? l.map((x) => x.id === c.id ? next : x) : l.filter((x) => x.id !== c.id))} />
          ))}
        </ul>
      )}
    </section>
  );
}
