import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import type { Cell } from "../api";
import { useI18n, type Lang } from "../i18n";

export interface SheetHandle { selection: () => { cell: string } }
type At = { r: number; c: number };

export const colName = (i: number): string => (i >= 26 ? colName(Math.floor(i / 26) - 1) : "") + String.fromCharCode(65 + (i % 26));

/** What the user typed, as a cell: a number (with a decimal comma in Italian), a formula, or text. */
function parse(text: string, lang: Lang): Cell {
  const t = text.trim();
  if (!t) return null;
  const n = lang === "it" && /^-?\d+,\d+$/.test(t) ? t.replace(",", ".") : t;
  return /^-?(\d+\.?\d*|\.\d+)$/.test(n) && !/^-?0\d/.test(n) ? Number(n) : text;
}

function show(v: Cell | undefined, lang: Lang): string {
  if (typeof v === "number") return v.toLocaleString(lang, { maximumFractionDigits: 10, useGrouping: false });
  if (typeof v === "boolean") return lang === "it" ? (v ? "VERO" : "FALSO") : v ? "TRUE" : "FALSE";
  return v ?? "";
}

const same = (a: Cell[] | undefined, b: Cell[] | undefined) =>
  a === b || (!!a && !!b && a.length === b.length && a.every((v, i) => v === b[i]));

interface Handlers {
  pick: (at: At) => void; edit: (at: At) => void; type: (text: string) => void;
  key: (e: React.KeyboardEvent) => void; done: () => void;
}

/** Rows are drawn again only when what they show changes; `on` must therefore keep the same identity. */
const Row = memo(function Row({ r, cols, vals, active, edit, lang, on: h }: {
  r: number; cols: number; vals: Cell[] | undefined; active: number; edit: string | null; lang: Lang; on: Handlers;
}) {
  return (
    <tr>
      <th className={`sticky left-0 z-10 w-10 min-w-10 border-b border-r border-slate-200 text-xs font-normal ${active >= 0 ? "bg-accent-soft text-accent" : "bg-slate-50 text-slate-400"}`}>{r + 1}</th>
      {Array.from({ length: cols }, (_, c) => {
        const v = vals?.[c];
        const on = c === active;
        return (
          <td key={c} data-on={on || undefined} onMouseDown={() => h.pick({ r, c })} onDoubleClick={() => h.edit({ r, c })} title={typeof v === "string" && v.length > 14 ? v : undefined}
            className={`relative h-7 w-28 min-w-28 max-w-28 px-1.5 border-b border-r border-slate-200 text-sm truncate cursor-cell scroll-mt-8 scroll-ml-11
              ${typeof v === "number" ? "text-right tabular-nums" : ""} ${typeof v === "string" && v.startsWith("#") ? "text-red-600" : ""} ${on ? "outline outline-2 -outline-offset-1 outline-accent z-[5]" : ""}`}>
            {on && edit !== null
              ? <input autoFocus value={edit} onChange={(e) => h.type(e.target.value)} onKeyDown={h.key} onBlur={h.done}
                  className="absolute inset-0 w-full px-1.5 text-sm bg-white outline-none text-left select-text" />
              : show(v, lang)}
          </td>
        );
      })}
    </tr>
  );
}, (a, b) => a.cols === b.cols && a.active === b.active && a.edit === b.edit && a.lang === b.lang && a.on === b.on && same(a.vals, b.vals));

/** A grid of cells. `rows` holds what was typed (numbers, text, formulas), `values` what each cell shows.
 *  Enter or a double click edits a cell; typing replaces it; ⌘Z undoes; a block copied from Excel can be pasted. */
export default forwardRef<SheetHandle, {
  rows: Cell[][]; values: Cell[][]; locked: boolean;
  onChange: (rows: Cell[][]) => void; onShift: (axis: "row" | "col", index: number, delta: number) => void;
}>(function SheetEditor({ rows, values, locked, onChange, onShift }, ref) {
  const { t, lang } = useI18n();
  const [sel, setSel] = useState<At>({ r: 0, c: 0 });
  const [edit, setEdit] = useState<string | null>(null);     // the text being typed into the selected cell
  const [inBar, setInBar] = useState(false);                 // typed in the formula bar, not in the cell
  const grid = useRef<HTMLDivElement>(null);
  const undo = useRef<Cell[][][]>([]);
  const redo = useRef<Cell[][][]>([]);
  const nRows = Math.min(Math.max(rows.length + 20, 40), 2000);
  const nCols = Math.min(Math.max(...rows.map((r) => r.length), 8) + 4, 100);
  const raw = rows[sel.r]?.[sel.c] ?? null;

  useImperativeHandle(ref, () => ({ selection: () => ({ cell: colName(sel.c) + (sel.r + 1) }) }), [sel]);
  useEffect(() => { grid.current?.querySelector("td[data-on]")?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [sel]);

  const change = (next: Cell[][]) => { undo.current.push(rows); redo.current = []; onChange(next); };
  const step = (from: React.MutableRefObject<Cell[][][]>, to: React.MutableRefObject<Cell[][][]>) => {
    const back = from.current.pop();
    if (back) { to.current.push(rows); onChange(back); }
  };
  /** Writes a block of cells with its top-left corner at `at`. */
  const write = (at: At, block: Cell[][]) => {
    const next = rows.map((r) => r.slice());
    block.forEach((line, i) => line.forEach((v, j) => {
      while (next.length <= at.r + i) next.push([]);
      const row = next[at.r + i];
      while (row.length <= at.c + j) row.push(null);
      row[at.c + j] = v;
    }));
    change(next);
  };
  const move = (dr: number, dc: number) => setSel((s) => ({ r: Math.min(Math.max(s.r + dr, 0), nRows - 1), c: Math.min(Math.max(s.c + dc, 0), nCols - 1) }));
  const begin = (text: string, bar = false) => { if (!locked) { setEdit(text); setInBar(bar); } };
  const end = (commit: boolean) => {
    if (edit === null) return;
    if (commit && parse(edit, lang) !== raw) write(sel, [[parse(edit, lang)]]);
    setEdit(null);
    setInBar(false);
    grid.current?.focus();
  };
  const editKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); end(true); move(e.shiftKey ? -1 : 1, 0); }
    else if (e.key === "Tab") { e.preventDefault(); end(true); move(0, e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); end(false); }
    e.stopPropagation();
  };
  const gridKey = (e: React.KeyboardEvent) => {
    const arrows: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const mod = e.metaKey || e.ctrlKey;
    if (arrows[e.key]) move(...arrows[e.key]);
    else if (e.key === "Tab") move(0, e.shiftKey ? -1 : 1);
    else if (locked) return;
    else if (mod && e.key.toLowerCase() === "z") step(e.shiftKey ? redo : undo, e.shiftKey ? undo : redo);
    else if (mod && e.key.toLowerCase() === "y") step(redo, undo);
    else if (e.key === "Enter" || e.key === "F2") begin(show(raw, lang));
    else if (e.key === "Backspace" || e.key === "Delete") { if (raw !== null) write(sel, [[null]]); }
    else if (e.key.length === 1 && !mod && !e.altKey) begin(e.key);
    else return;
    e.preventDefault();
  };
  const copy = (e: React.ClipboardEvent, cut = false) => {
    if (edit !== null) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", show(raw, lang));
    if (cut && !locked && raw !== null) write(sel, [[null]]);
  };
  const paste = (e: React.ClipboardEvent) => {
    if (edit !== null || locked) return;
    e.preventDefault();
    const lines = e.clipboardData.getData("text/plain").replace(/\r?\n$/, "").split(/\r?\n/);
    write(sel, lines.map((line) => line.split("\t").map((v) => parse(v, lang))));
  };

  // The rows keep one set of handlers; these always reach the latest state through `live`.
  const live = useRef<Handlers>(null!);
  live.current = {
    pick: (at) => { if (edit !== null) end(true); setSel(at); },
    edit: (at) => { setSel(at); begin(show(rows[at.r]?.[at.c], lang)); },
    type: setEdit, key: editKey, done: () => end(true),
  };
  const on = useMemo<Handlers>(() => ({
    pick: (at) => live.current.pick(at), edit: (at) => live.current.edit(at), type: (text) => live.current.type(text),
    key: (e) => live.current.key(e), done: () => live.current.done(),
  }), []);

  const tool = (label: string, title: string, icon: React.ReactNode, act: () => void) => (
    <button title={title} disabled={locked} onClick={act}
      className="h-8 px-2 rounded-md flex items-center gap-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-30">{icon}{label}</button>
  );
  return (
    <div className="flex-1 min-h-0 flex flex-col bg-white">
      <div className="flex items-center gap-0.5 px-4 py-1.5 border-b border-slate-200">
        {tool(t("tb_row"), t("sh_insert_row"), <Plus size={12} />, () => onShift("row", sel.r, 1))}
        {tool(t("tb_row"), t("sh_delete_row"), <Minus size={12} />, () => onShift("row", sel.r, -1))}
        <span className="w-px h-5 bg-slate-200 mx-1" />
        {tool(t("tb_col"), t("sh_insert_col"), <Plus size={12} />, () => onShift("col", sel.c, 1))}
        {tool(t("tb_col"), t("sh_delete_col"), <Minus size={12} />, () => onShift("col", sel.c, -1))}
      </div>
      <div className="flex items-center border-b border-slate-200 text-sm">
        <span className="w-16 py-1.5 text-center text-slate-500 border-r border-slate-200 font-mono text-xs">{colName(sel.c)}{sel.r + 1}</span>
        <span className="px-2 text-slate-400 italic font-serif">fx</span>
        <input value={edit ?? show(raw, lang)} readOnly={locked} onChange={(e) => begin(e.target.value, true)} onKeyDown={editKey}
          onBlur={() => { if (inBar) end(true); }} className="flex-1 py-1.5 pr-3 outline-none bg-transparent font-mono text-[13px]" />
      </div>
      <div ref={grid} tabIndex={0} onKeyDown={gridKey} onCopy={copy} onCut={(e) => copy(e, true)} onPaste={paste}
        className={`flex-1 overflow-auto outline-none select-none ${locked ? "opacity-70" : ""}`}>
        <table className="border-separate border-spacing-0 table-fixed">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 w-10 min-w-10 h-7 bg-slate-50 border-b border-r border-slate-200" />
              {Array.from({ length: nCols }, (_, c) => (
                <th key={c} className={`sticky top-0 z-10 h-7 w-28 min-w-28 border-b border-r border-slate-200 text-xs font-normal ${c === sel.c ? "bg-accent-soft text-accent" : "bg-slate-50 text-slate-400"}`}>{colName(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: nRows }, (_, r) => (
              <Row key={r} r={r} cols={nCols} vals={sel.r === r && edit !== null && inBar ? withCell(values[r], sel.c, edit) : values[r]}
                active={sel.r === r ? sel.c : -1} edit={sel.r === r && !inBar ? edit : null} lang={lang} on={on} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
});

function withCell(vals: Cell[] | undefined, c: number, text: string): Cell[] {
  const out = (vals ?? []).slice();
  while (out.length <= c) out.push(null);
  out[c] = text;
  return out;
}
