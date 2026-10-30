import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, BookOpen, Building2, CalendarClock, Check, Download, FileText, FolderKanban, GitMerge, Loader2,
  Pencil, RefreshCw, Search, Trash2, User, X,
} from "lucide-react";
import { api, type DocRef, type EntityType, type Fact, type ReviewItem, type WikiEntity, type WikiOverview } from "../api";
import { useI18n } from "../i18n";

const TYPE_ICON = { organization: Building2, person: User, project: FolderKanban } as const;
const TYPES: EntityType[] = ["organization", "person", "project"];
type OpenDoc = (docId: string, page: number) => void;

function SourceChip({ src, onOpenDoc }: { src: DocRef; onOpenDoc: OpenDoc }) {
  return (
    <button onClick={() => onOpenDoc(src.doc_id, src.page)}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-100 text-[11px] text-slate-600 hover:bg-accent-soft hover:text-accent max-w-56">
      <FileText size={11} className="shrink-0" /><span className="truncate">{src.doc_name}</span><span className="shrink-0">p.{src.page}</span>
    </button>
  );
}

function fmtDate(iso: string | null, lang: string) {
  if (!iso) return "";
  return new Date(iso + "T00:00:00").toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
}

// --- overview: review list, deadlines, log ---

function ReviewCard({ item, onDone, onOpenDoc, onOpenEntity }: {
  item: ReviewItem; onDone: () => void; onOpenDoc: OpenDoc; onOpenEntity: (id: number) => void;
}) {
  const { t } = useI18n();
  if (item.kind === "conflict")
    return (
      <div className="p-4 rounded-xl border border-amber-200 bg-amber-50/60">
        <div className="flex items-center gap-2 text-sm font-medium text-amber-900">
          <AlertTriangle size={16} />
          <button onClick={() => onOpenEntity(item.entity_id)} className="hover:underline text-left">
            {t("conflict_title", { key: t(`key_${item.key}` as never), name: item.name })}
          </button>
        </div>
        <div className="mt-3 space-y-2">
          {item.options.map((o) => (
            <div key={o.value} className="flex items-center gap-3 bg-white rounded-lg border border-amber-100 px-3 py-2">
              <span className="font-mono text-sm">{o.value}</span>
              <div className="flex flex-wrap gap-1">{o.sources.map((s, i) => <SourceChip key={i} src={s} onOpenDoc={onOpenDoc} />)}</div>
              <button onClick={() => api.resolveConflict(item.entity_id, item.key, o.fact_ids[0]).then(onDone)}
                className="ml-auto shrink-0 flex items-center gap-1 text-sm px-2.5 py-1 rounded-md border border-slate-200 hover:border-accent hover:text-accent">
                <Check size={14} /> {t("keep_this")}
              </button>
            </div>
          ))}
        </div>
      </div>
    );
  const { a, b } = item;
  return (
    <div className="p-4 rounded-xl border border-slate-200 bg-white">
      <div className="flex items-center gap-2 text-sm font-medium">
        <GitMerge size={16} className="text-accent" /> {t("dup_title")}
        {item.same_id && <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">{t("dup_same_id")}</span>}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[15px]">
        <button onClick={() => onOpenEntity(a.id)} className="text-accent hover:underline">{a.name}</button>
        <span className="text-slate-400">↔</span>
        <button onClick={() => onOpenEntity(b.id)} className="text-accent hover:underline">{b.name}</button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-sm">
        {[[a, b], [b, a]].map(([keep, other]) => (
          <button key={keep.id} onClick={() => api.merge(keep.id, other.id).then(onDone)}
            className="px-2.5 py-1 rounded-md border border-slate-200 hover:border-accent hover:text-accent">{t("merge_into", { name: keep.name })}</button>
        ))}
        <button onClick={() => api.distinct(a.id, b.id).then(onDone)} className="px-2.5 py-1 rounded-md text-slate-500 hover:bg-slate-100">{t("not_same")}</button>
      </div>
    </div>
  );
}

function Overview({ data, refresh, onOpenDoc, onOpenEntity }: {
  data: WikiOverview; refresh: () => void; onOpenDoc: OpenDoc; onOpenEntity: (id: number) => void;
}) {
  const { t, lang } = useI18n();
  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <div className="flex items-start gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("wiki_title")}</h1>
          <p className="text-slate-500 mt-1 max-w-2xl">{t("wiki_subtitle")}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-sm">
        {TYPES.map((type) => {
          const Icon = TYPE_ICON[type];
          return (
            <span key={type} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white border border-slate-200">
              <Icon size={15} className="text-slate-500" /> <b>{data.entities.filter((e) => e.type === type).length}</b> {t(`type_${type}` as never)}
            </span>
          );
        })}
        {data.pending_docs > 0 && (
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-soft text-accent">
            <Loader2 size={14} className="animate-spin" /> {t("wiki_pending", { n: data.pending_docs })}
          </span>
        )}
        <a href={api.exportUrl(lang)} className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50">
          <Download size={15} /> {t("export_obsidian")}
        </a>
      </div>

      <section className="mt-8">
        <h2 className="flex items-center gap-2 font-semibold mb-3"><AlertTriangle size={17} className="text-amber-600" /> {t("review")}
          {data.review.length > 0 && <span className="text-xs px-1.5 rounded-full bg-amber-100 text-amber-800">{data.review.length}</span>}
        </h2>
        {data.review.length === 0 ? <p className="text-sm text-slate-500">{t("review_none")}</p> : (
          <div className="space-y-3">
            {data.review.map((item, i) => <ReviewCard key={i} item={item} onDone={refresh} onOpenDoc={onOpenDoc} onOpenEntity={onOpenEntity} />)}
          </div>
        )}
      </section>

      <section className="mt-8">
        <h2 className="flex items-center gap-2 font-semibold mb-3"><CalendarClock size={17} className="text-accent" /> {t("upcoming")}</h2>
        {data.deadlines.length === 0 ? <p className="text-sm text-slate-500">{t("no_deadlines")}</p> : (
          <ul className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
            {data.deadlines.map((d, i) => (
              <li key={i} className="flex items-center gap-4 px-4 py-3">
                <span className={`w-28 shrink-0 text-sm font-medium tabular-nums ${d.overdue ? "text-red-600" : ""}`}>
                  {fmtDate(d.date, lang)}{d.overdue && <span className="block text-[11px] font-normal">{t("overdue")}</span>}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block">{d.value}</span>
                  <span className="flex flex-wrap gap-2 mt-0.5 text-sm">
                    {d.entities.map((e) => <button key={e.id} onClick={() => onOpenEntity(e.id)} className="text-accent hover:underline">{e.name}</button>)}
                  </span>
                </span>
                <SourceChip src={d} onOpenDoc={onOpenDoc} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-semibold mb-3">{t("recent")}</h2>
        <ul className="text-sm space-y-1.5 text-slate-600">
          {data.log.map((l, i) => (
            <li key={i} className="flex gap-3">
              <span className="text-slate-400 tabular-nums w-24 shrink-0">{fmtDate(l.ts.slice(0, 10), lang)}</span>
              <span className="w-14 shrink-0 text-[11px] uppercase tracking-wide text-slate-400 pt-0.5">{l.action}</span>
              <span>{l.detail}</span>
            </li>
          ))}
        </ul>
        <button onClick={() => { if (confirm(t("confirm_rebuild"))) api.rebuildWiki().then(refresh); }}
          className="mt-6 flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800">
          <RefreshCw size={14} /> {t("rebuild")}
        </button>
      </section>
    </div>
  );
}

// --- entity page ---

function FactRow({ f, conflict, onOpenDoc, onReject }: { f: Fact; conflict: boolean; onOpenDoc: OpenDoc; onReject: () => void }) {
  const { t } = useI18n();
  return (
    <li className={`group flex items-center gap-3 px-4 py-2.5 ${conflict ? "bg-amber-50/70" : ""}`}>
      <span className="w-32 shrink-0 text-sm text-slate-500">{t(`key_${f.key}` as never)}</span>
      <span className={`flex-1 ${["vat_id", "tax_code", "iban", "phone"].includes(f.key) ? "font-mono text-sm" : ""}`}>{f.value}</span>
      {conflict && <span className="text-[11px] text-amber-700 flex items-center gap-1"><AlertTriangle size={12} /> {t("conflict_badge")}</span>}
      <SourceChip src={f} onOpenDoc={onOpenDoc} />
      <button onClick={onReject} title={t("reject_fact")} className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-400 hover:text-red-600 hover:bg-red-50"><X size={14} /></button>
    </li>
  );
}

function dedup(facts: Fact[]) {
  const seen = new Set<string>();
  return facts.filter((f) => {
    const k = `${f.kind}|${f.key}|${f.value.toLowerCase()}|${f.date}|${f.target_id}`;
    return seen.has(k) ? false : (seen.add(k), true);
  });
}

function EntityPage({ id, onOpenDoc, onOpenEntity, onChanged }: {
  id: number; onOpenDoc: OpenDoc; onOpenEntity: (id: number | null) => void; onChanged: () => void;
}) {
  const { t, lang } = useI18n();
  const [e, setE] = useState<WikiEntity | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const load = useCallback(() => api.entity(id).then((x) => { setE(x); setNotes(x.notes); }).catch(() => onOpenEntity(null)), [id, onOpenEntity]);
  useEffect(() => { setE(null); setEditing(null); load(); }, [load]);
  if (!e) return <div className="p-10"><Loader2 className="animate-spin text-slate-400" /></div>;

  const facts = dedup(e.facts);
  const of = (kind: Fact["kind"]) => facts.filter((f) => f.kind === kind);
  const roles = [...new Set(of("role").map((f) => f.value.toLowerCase()))];
  const Icon = TYPE_ICON[e.type];
  const reject = (f: Fact) => api.rejectFact(f.id).then(() => { load(); onChanged(); });
  const saveSummary = (summary: string) => api.editEntity(id, { summary }).then((x) => { setE(x); setEditing(null); onChanged(); });

  const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <section className="mt-7">
      <h2 className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">{title}</h2>
      {children}
    </section>
  );
  const timeline = (items: Fact[]) => (
    <ul className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
      {items.map((f) => (
        <li key={f.id} className="group flex items-center gap-4 px-4 py-2.5">
          <span className="w-28 shrink-0 text-sm tabular-nums text-slate-500">{fmtDate(f.date, lang) || "·"}</span>
          <span className="flex-1">{f.value}</span>
          <SourceChip src={f} onOpenDoc={onOpenDoc} />
          <button onClick={() => reject(f)} title={t("reject_fact")} className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-400 hover:text-red-600"><X size={14} /></button>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Icon size={15} /> {t(`one_${e.type}` as never)}
        {roles.length > 0 && <>· {t("roles")}: {roles.map((r) => <span key={r} className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-xs">{r}</span>)}</>}
        <span className="ml-auto">{t("in_docs", { n: e.documents.length })}</span>
      </div>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">{e.name}</h1>

      <div className="mt-4">
        {editing !== null ? (
          <div>
            <textarea value={editing} onChange={(ev) => setEditing(ev.target.value)} rows={4} autoFocus
              className="w-full p-3 rounded-lg border border-slate-300 outline-none focus:border-accent text-[15px]" />
            <div className="mt-2 flex gap-2 text-sm">
              <button onClick={() => saveSummary(editing)} className="px-3 py-1.5 rounded-lg bg-accent text-white">{t("save")}</button>
              <button onClick={() => setEditing(null)} className="px-3 py-1.5 rounded-lg border border-slate-200">{t("cancel")}</button>
            </div>
          </div>
        ) : (
          <div className="group">
            <p className="text-[16px] leading-relaxed text-slate-700">{e.summary || <Loader2 size={16} className="animate-spin text-slate-300" />}</p>
            <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-400">
              <span>{e.summary_edited ? t("summary_edited") : t("summary_auto")}</span>
              <button onClick={() => setEditing(e.summary)} className="flex items-center gap-1 hover:text-accent"><Pencil size={12} /> {t("edit")}</button>
              {!!e.summary_edited && <button onClick={() => saveSummary("")} className="hover:text-accent">{t("reset_auto")}</button>}
            </div>
          </div>
        )}
      </div>

      {of("attr").length > 0 && (
        <Section title={t("sec_data")}>
          <ul className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
            {of("attr").map((f) => <FactRow key={f.id} f={f} conflict={e.conflict_keys.includes(f.key)} onOpenDoc={onOpenDoc} onReject={() => reject(f)} />)}
          </ul>
        </Section>
      )}

      {(of("relation").length > 0 || e.backlinks.length > 0) && (
        <Section title={t("sec_links")}>
          <ul className="bg-white rounded-xl border border-slate-200 divide-y divide-slate-100">
            {of("relation").filter((f) => f.target_id).map((f) => (
              <li key={f.id} className="flex items-center gap-2 px-4 py-2.5">
                <span className="text-slate-500">{f.value}</span>
                <button onClick={() => onOpenEntity(f.target_id)} className="text-accent font-medium hover:underline">{f.target_name}</button>
                <span className="ml-auto"><SourceChip src={f} onOpenDoc={onOpenDoc} /></span>
              </li>
            ))}
            {e.backlinks.map((b, i) => (
              <li key={`b${i}`} className="flex items-center gap-2 px-4 py-2.5">
                <button onClick={() => onOpenEntity(b.id)} className="text-accent font-medium hover:underline">{b.name}</button>
                <span className="text-slate-500">{b.label}</span>
                <span className="ml-auto"><SourceChip src={b} onOpenDoc={onOpenDoc} /></span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {of("deadline").length > 0 && <Section title={t("sec_deadlines")}>{timeline(of("deadline"))}</Section>}
      {of("event").length > 0 && <Section title={t("sec_events")}>{timeline(of("event"))}</Section>}

      <Section title={t("sec_docs")}>
        <ul className="space-y-2">
          {e.documents.map((d) => (
            <li key={d.id}>
              <button onClick={() => onOpenDoc(d.id, 1)} className="w-full text-left flex gap-3 p-3 rounded-xl bg-white border border-slate-200 hover:border-accent">
                <FileText size={18} className="text-slate-400 shrink-0 mt-0.5" />
                <span className="min-w-0">
                  <span className="font-medium">{d.name}</span>
                  <span className="text-xs text-slate-400 ml-2">{fmtDate(d.doc_date, lang)}</span>
                  {d.summary && <span className="block text-sm text-slate-500 mt-0.5 line-clamp-2">{d.summary}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("sec_notes")}>
        <textarea value={notes} onChange={(ev) => setNotes(ev.target.value)} rows={3} placeholder={t("notes_placeholder")}
          onBlur={() => { if (notes !== e.notes) api.editEntity(id, { notes }).then(setE); }}
          className="w-full p-3 rounded-xl border border-slate-200 bg-white outline-none focus:border-accent text-[15px]" />
      </Section>

      <button onClick={() => { if (confirm(t("confirm_delete_page", { name: e.name }))) api.deleteEntity(id).then(() => { onChanged(); onOpenEntity(null); }); }}
        className="mt-8 flex items-center gap-1.5 text-sm text-slate-400 hover:text-red-600"><Trash2 size={14} /> {t("delete_page")}</button>
    </div>
  );
}

// --- shell: list on the left, overview or page on the right ---

export default function Wiki({ entityId, setEntityId, onOpenDoc }: {
  entityId: number | null; setEntityId: (id: number | null) => void; onOpenDoc: OpenDoc;
}) {
  const { t } = useI18n();
  const [data, setData] = useState<WikiOverview | null>(null);
  const [q, setQ] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  const refresh = useCallback(() => api.wiki().then((x) => { setData(x); setFailed(null); }).catch((e) => setFailed(String(e))), []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, data?.pending_docs ? 3000 : 15000);
    return () => clearInterval(timer);
  }, [refresh, data?.pending_docs]);

  const shown = (data?.entities ?? []).filter((e) => e.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="h-full flex">
      <aside className="w-72 shrink-0 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-3">
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("wiki_search")}
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-200 text-sm outline-none focus:border-accent" />
          </div>
        </div>
        <button onClick={() => setEntityId(null)}
          className={`mx-3 flex items-center gap-2 px-3 py-2 rounded-lg text-sm ${entityId === null ? "bg-accent-soft text-accent font-medium" : "hover:bg-slate-100"}`}>
          <BookOpen size={16} /> {t("wiki_home")}
          {!!data?.review.length && <span className="ml-auto text-xs px-1.5 rounded-full bg-amber-100 text-amber-800">{data.review.length}</span>}
        </button>
        <nav className="flex-1 overflow-auto px-3 pb-4">
          {TYPES.map((type) => {
            const group = shown.filter((e) => e.type === type);
            if (!group.length) return null;
            const Icon = TYPE_ICON[type];
            return (
              <div key={type} className="mt-4">
                <div className="px-3 mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
                  <Icon size={12} /> {t(`type_${type}` as never)}
                </div>
                {group.map((e) => (
                  <button key={e.id} onClick={() => setEntityId(e.id)}
                    className={`w-full text-left flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm ${entityId === e.id ? "bg-accent-soft text-accent font-medium" : "text-slate-700 hover:bg-slate-100"}`}>
                    <span className="truncate">{e.name}</span>
                    <span className="ml-auto text-xs text-slate-400">{e.doc_count}</span>
                  </button>
                ))}
              </div>
            );
          })}
          {data && data.entities.length === 0 && <p className="px-3 mt-6 text-sm text-slate-400">{t("wiki_empty")}</p>}
        </nav>
      </aside>
      <div className="flex-1 min-w-0 overflow-auto">
        {entityId !== null
          ? <EntityPage id={entityId} onOpenDoc={onOpenDoc} onOpenEntity={setEntityId} onChanged={refresh} />
          : data ? <Overview data={data} refresh={refresh} onOpenDoc={onOpenDoc} onOpenEntity={setEntityId} />
          : failed ? (
            <div className="p-10 max-w-xl">
              <div className="px-4 py-3 rounded-lg bg-red-50 text-red-700 text-sm">{t("error")} {failed}</div>
              <button onClick={refresh} className="mt-3 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm hover:bg-slate-50">{t("retry")}</button>
            </div>
          )
          : <div className="p-10"><Loader2 className="animate-spin text-slate-400" /></div>}
      </div>
    </div>
  );
}
