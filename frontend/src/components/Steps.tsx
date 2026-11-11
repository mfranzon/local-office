import { Check, Loader2, Plug } from "lucide-react";
import type { Doc, Step } from "../api";
import { useI18n } from "../i18n";

/** The tool calls of an assistant run, one per line; the last one spins while the run is still going. */
export default function Steps({ steps, running, docs = [] }: { steps: Step[]; running: boolean; docs?: Doc[] }) {
  const { t, d } = useI18n();
  return (
    <ul className="mt-2 space-y-1 text-sm text-slate-500">
      {steps.map((st, j) => {
        const named = st.tool === "read_document" ? docs.find((x) => x.id === st.detail)?.name ?? st.detail : st.detail;
        const detail = /^\d+$/.test(named) ? "" : named;   // a block or row number says nothing to the user
        return (
          <li key={j} className="flex items-center gap-2 min-w-0">
            {running && j === steps.length - 1 ? <Loader2 size={13} className="animate-spin text-accent shrink-0" /> : <Check size={13} className="text-emerald-600 shrink-0" />}
            <span className="shrink-0">{st.tool.includes("__")
              ? <span className="inline-flex items-center gap-1"><Plug size={12} className="text-amber-700" /> {st.tool.replace("__", " › ")}</span>
              : `step_${st.tool}` in d ? t(`step_${st.tool}` as never) : st.tool}</span>
            {detail && <span className="truncate text-slate-400">{detail}</span>}
          </li>
        );
      })}
    </ul>
  );
}
