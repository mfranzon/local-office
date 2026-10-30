import { useCallback, useEffect, useState } from "react";
import { BookOpen, FileText, Image as ImageIcon, MessageSquare, Settings as SettingsIcon, ShieldCheck } from "lucide-react";
import { api, type Doc, type Source } from "./api";
import { useI18n } from "./i18n";
import DocViewer from "./components/DocViewer";
import Documents from "./views/Documents";
import Ask from "./views/Ask";
import Images from "./views/Images";
import Settings from "./views/Settings";
import Wiki from "./views/Wiki";
import Setup from "./views/Setup";

type View = "ask" | "docs" | "wiki" | "images" | "settings";
export interface ViewerTarget { id: string; page: number }

function useDocuments() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const refresh = useCallback(() => api.documents().then(setDocs).catch(() => {}), []);
  const busy = docs.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, busy ? 1500 : 10000);
    return () => clearInterval(timer);
  }, [refresh, busy]);
  return { docs, refresh };
}

function useModelStatus() {
  const [models, setModels] = useState<Record<string, "running" | "ready" | "missing"> | null>(null);
  useEffect(() => {
    const check = () => api.status().then((s) => setModels(s.models)).catch(() => setModels(null));
    check();
    const timer = setInterval(check, 15000);
    return () => clearInterval(timer);
  }, []);
  return models;
}

export default function App() {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  useEffect(() => { api.setup().then((s) => setNeedsSetup(s.missing.length > 0)).catch(() => setNeedsSetup(false)); }, []);
  if (needsSetup === null) return null;
  if (needsSetup) return <Setup onDone={() => setNeedsSetup(false)} />;
  return <Shell />;
}

function Shell() {
  const { t, lang, setLang } = useI18n();
  const [view, setView] = useState<View>("docs");
  const [scope, setScope] = useState<Doc | null>(null);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [wikiEntity, setWikiEntity] = useState<number | null>(null);
  const { docs, refresh } = useDocuments();
  const models = useModelStatus();

  // Land on "Ask" once there is something to ask about; first-time users land on the upload screen.
  const [landed, setLanded] = useState(false);
  useEffect(() => {
    if (!landed && docs.length) { setView("ask"); setLanded(true); }
  }, [docs.length, landed]);

  const askAbout = (doc: Doc) => { setScope(doc); setViewer(null); setView("ask"); };
  const openSource = (s: Source) => {
    if (s.kind === "wiki") { setWikiEntity(s.entity_id); setView("wiki"); }
    else if (s.doc_id) setViewer({ id: s.doc_id, page: s.page ?? 1 });
  };
  const nav: [View, string, typeof FileText][] = [
    ["ask", t("nav_ask"), MessageSquare],
    ["docs", t("nav_docs"), FileText],
    ["wiki", t("nav_wiki"), BookOpen],
    ["images", t("nav_images"), ImageIcon],
  ];
  const allUp = models && Object.values(models).every((m) => m !== "missing");
  const someUp = models && Object.values(models).some((m) => m !== "missing");

  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 bg-white border-r border-slate-200 flex flex-col">
        <div className="px-5 py-5 flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-accent grid place-items-center text-white"><FileText size={18} /></div>
          <span className="font-semibold text-lg tracking-tight">Local Office</span>
        </div>
        <nav className="px-3 space-y-1">
          {nav.map(([key, label, Icon]) => (
            <button key={key} onClick={() => setView(key)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[15px] transition ${view === key ? "bg-accent-soft text-accent font-medium" : "text-slate-600 hover:bg-slate-100"}`}>
              <Icon size={18} /> {label}
              {key === "docs" && docs.length > 0 && <span className="ml-auto text-xs text-slate-400">{docs.length}</span>}
            </button>
          ))}
        </nav>
        <div className="mt-auto px-3">
          <button onClick={() => setView("settings")}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[15px] transition ${view === "settings" ? "bg-accent-soft text-accent font-medium" : "text-slate-600 hover:bg-slate-100"}`}>
            <SettingsIcon size={18} /> {t("nav_settings")}
          </button>
        </div>
        <div className="p-4 space-y-3 text-xs text-slate-500 border-t border-slate-100 mt-2">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${allUp ? "bg-emerald-500" : someUp ? "bg-amber-400" : "bg-slate-300 animate-pulse"}`} />
            {allUp ? t("ai_ready") : someUp ? t("ai_partial") : t("ai_starting")}
          </div>
          <div className="flex gap-2 items-start"><ShieldCheck size={14} className="shrink-0 mt-0.5 text-emerald-600" />{t("privacy")}</div>
          <div className="flex rounded-md border border-slate-200 overflow-hidden w-fit">
            {(["it", "en"] as const).map((l) => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-2.5 py-1 uppercase ${lang === l ? "bg-slate-800 text-white" : "hover:bg-slate-100"}`}>{l}</button>
            ))}
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 overflow-hidden">
        {view === "docs" && <Documents docs={docs} refresh={refresh} onOpen={(id) => setViewer({ id, page: 1 })} onAsk={askAbout} />}
        {/* always mounted: the conversation (and an answer still being written) survives switching views */}
        <div className={view === "ask" ? "h-full" : "hidden"}><Ask docs={docs} scope={scope} setScope={setScope} onSource={openSource} /></div>
        {view === "wiki" && <Wiki entityId={wikiEntity} setEntityId={setWikiEntity} onOpenDoc={(id, page) => setViewer({ id, page })} />}
        {view === "images" && <Images refreshDocs={refresh} />}
        {view === "settings" && <Settings />}
      </main>

      {viewer && <DocViewer target={viewer} onClose={() => setViewer(null)} onAsk={askAbout} />}
    </div>
  );
}
