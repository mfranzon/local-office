import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import { ArrowDown, ArrowUp, Bold, Eraser, ImageOff, ImagePlus, Italic, Layers, List, ListOrdered, Palette, Plus, Trash2 } from "lucide-react";
import { api, type Slide } from "../api";
import { useI18n } from "../i18n";
import { toHtml, toMarkdown } from "../desk/markdown";

export interface SlidesHandle { selection: () => { slide: number }; go: (slide: number) => void }
const EMPTY: Slide = { title: "", body: "", notes: "" };
/** Whether text on this colour has to be white to be read (as in the PowerPoint file that is downloaded). */
const dark = (c?: string) => !!c && 0.299 * parseInt(c.slice(1, 3), 16) + 0.587 * parseInt(c.slice(3, 5), 16) + 0.114 * parseInt(c.slice(5, 7), 16) < 140;

/** The slide being written: its title, its body (Markdown, edited as text with lists), its background colour,
 *  its image and the speaker notes.
 *  It is mounted again for every slide shown, so the body's editor always starts from that slide's text. */
function Stage({ slide, locked, onChange, onBackgroundAll, children }: {
  slide: Slide; locked: boolean; onChange: (patch: Partial<Slide>) => void; onBackgroundAll: () => void; children: React.ReactNode;
}) {
  const { t } = useI18n();
  const change = useRef(onChange);
  change.current = onChange;
  const [failed, setFailed] = useState<string | null>(null);     // why the image that was chosen can't be used
  const editor = useEditor({
    extensions: [StarterKit.configure({ underline: false, link: { openOnClick: false } }), TableKit.configure({ table: { resizable: false } })],
    content: toHtml(slide.body),
    shouldRerenderOnTransaction: true,          // the toolbar shows what is active at the cursor
    editorProps: { attributes: { class: "desk-doc desk-slide", spellcheck: "true" } },
    onUpdate: ({ editor }) => change.current({ body: toMarkdown(editor.getJSON()) }),
  });
  useEffect(() => { editor.setEditable(!locked, false); }, [locked, editor]);
  const run = () => editor.chain().focus();
  const button = (title: string, on: boolean, act: () => void, icon: React.ReactNode) => (
    <button title={title} disabled={locked} onMouseDown={(e) => e.preventDefault()} onClick={act}
      className={`h-8 min-w-8 px-1.5 rounded-md flex items-center justify-center disabled:opacity-30 ${on ? "bg-accent-soft text-accent" : "text-slate-600 hover:bg-slate-100"}`}>
      {icon}
    </button>
  );
  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <div className="flex items-center gap-0.5 px-4 py-1.5 border-b border-slate-200 bg-white">
        {button(t("tb_bold"), editor.isActive("bold"), () => run().toggleBold().run(), <Bold size={16} />)}
        {button(t("tb_italic"), editor.isActive("italic"), () => run().toggleItalic().run(), <Italic size={16} />)}
        <span className="w-px h-5 bg-slate-200 mx-1" />
        {button(t("tb_bullets"), editor.isActive("bulletList"), () => run().toggleBulletList().run(), <List size={16} />)}
        {button(t("tb_numbers"), editor.isActive("orderedList"), () => run().toggleOrderedList().run(), <ListOrdered size={16} />)}
        <span className="w-px h-5 bg-slate-200 mx-1" />
        <label title={t("sl_background")}
          className={`relative h-8 min-w-8 px-1.5 rounded-md flex items-center justify-center gap-1 text-slate-600 ${locked ? "opacity-30" : "hover:bg-slate-100 cursor-pointer"}`}>
          <Palette size={16} />
          <span className="w-3.5 h-3.5 rounded-sm border border-slate-300" style={{ background: slide.background ?? "#fff" }} />
          <input type="color" disabled={locked} value={slide.background ?? "#ffffff"} onChange={(e) => onChange({ background: e.target.value })}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-default" />
        </label>
        {slide.background && button(t("sl_background_all"), false, onBackgroundAll, <Layers size={16} />)}
        {slide.background && button(t("sl_background_none"), false, () => onChange({ background: undefined }), <Eraser size={16} />)}
        <label title={t("sl_image")}
          className={`h-8 min-w-8 px-1.5 rounded-md flex items-center justify-center text-slate-600 ${locked ? "opacity-30" : "hover:bg-slate-100 cursor-pointer"}`}>
          <ImagePlus size={16} />
          <input type="file" accept="image/*" disabled={locked} className="hidden" onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) { setFailed(null); api.deskImage(file).then(({ image }) => change.current({ image })).catch((err) => setFailed((err as Error).message)); }
          }} />
        </label>
        {slide.image && button(t("sl_image_none"), false, () => onChange({ image: undefined }), <ImageOff size={16} />)}
        {failed && <span className="ml-2 text-xs text-red-600 truncate">{failed}</span>}
        <div className="ml-auto flex items-center gap-0.5">{children}</div>
      </div>
      <div className={`flex-1 overflow-auto bg-slate-100 p-6 ${locked ? "opacity-70" : ""}`}>
        <div style={{ background: slide.background }}
          className={`desk-stage max-w-[960px] mx-auto aspect-video flex flex-col bg-white border border-slate-200 shadow-sm rounded-sm ${dark(slide.background) ? "dark" : ""}`}>
          <input value={slide.title} readOnly={locked} placeholder={t("sl_title")} onChange={(e) => onChange({ title: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") editor.commands.focus("start"); }}
            className="slide-title w-full bg-transparent outline-none font-semibold tracking-tight placeholder:text-slate-300" />
          <div className="flex-1 min-h-0 flex gap-[2cqw]">
            <EditorContent editor={editor} className="flex-1 min-w-0 overflow-auto" />
            {slide.image && <img src={api.deskImageUrl(slide.image)} alt="" className="w-1/2 min-h-0 object-contain" />}
          </div>
        </div>
        <textarea value={slide.notes} readOnly={locked} placeholder={t("sl_notes")} rows={3} onChange={(e) => onChange({ notes: e.target.value })}
          className="block w-full max-w-[960px] mx-auto mt-4 px-4 py-3 rounded-lg border border-slate-200 bg-white text-sm outline-none resize-y focus:border-accent" />
      </div>
    </div>
  );
}

/** A presentation to write: the slides in a strip on the left, the one being written next to it. `rev` changes
 *  when the slides come from the server (the assistant edited them, or its changes were undone). */
export default forwardRef<SlidesHandle, { slides: Slide[]; rev: number; locked: boolean; onChange: (slides: Slide[]) => void }>(
  function SlidesEditor({ slides, rev, locked, onChange }, ref) {
    const { t } = useI18n();
    const [at, setAt] = useState(0);
    const [moves, setMoves] = useState(0);          // goes up when slides are added, deleted or moved
    const deck = slides.length ? slides : [EMPTY];  // a new presentation shows a first slide to write on
    const i = Math.min(at, deck.length - 1);
    useImperativeHandle(ref, () => ({ selection: () => ({ slide: i + 1 }), go: (slide) => setAt(Math.max(slide - 1, 0)) }), [i]);

    const arrange = (next: Slide[], to: number) => { onChange(next); setAt(to); setMoves((m) => m + 1); };
    const swap = (j: number) => arrange(deck.map((s, n) => n === i ? deck[j] : n === j ? deck[i] : s), j);
    const action = (title: string, act: () => void, icon: React.ReactNode, disabled = false) => (
      <button title={title} disabled={locked || disabled} onClick={act}
        className="h-8 px-2 rounded-md flex items-center gap-1 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-30">{icon}</button>
    );
    return (
      <div className="flex-1 min-h-0 flex">
        <nav className="w-48 shrink-0 overflow-auto border-r border-slate-200 bg-white p-3 space-y-2">
          {deck.map((s, n) => (
            <div key={n} className="flex gap-2" ref={n === i ? (el) => el?.scrollIntoView({ block: "nearest" }) : undefined}>
              <span className="w-4 text-right text-xs text-slate-400">{n + 1}</span>
              <button onClick={() => setAt(n)}
                style={{ background: s.background }}
                className={`desk-stage relative flex-1 min-w-0 aspect-video overflow-hidden text-left rounded border bg-white ${dark(s.background) ? "dark" : ""} ${n === i ? "border-accent ring-2 ring-accent-soft" : "border-slate-200 hover:border-slate-400"}`}>
                <span className="slide-title block font-semibold truncate">{s.title}</span>
                <span className={`slide-thumb block whitespace-pre-line ${s.image ? "w-1/2" : ""}`}>{s.body.replace(/[*`\\|]|^#+\s/gm, "")}</span>
                {s.image && <img src={api.deskImageUrl(s.image)} alt="" className="absolute right-[6%] bottom-[8%] w-[40%] h-[55%] object-contain" />}
              </button>
            </div>
          ))}
        </nav>
        <Stage key={`${rev}.${moves}.${i}`} slide={deck[i]} locked={locked} onChange={(patch) => onChange(deck.map((s, n) => n === i ? { ...s, ...patch } : s))}
          onBackgroundAll={() => onChange(deck.map((s) => ({ ...s, background: deck[i].background })))}>
          {action(t("sl_add"), () => arrange([...deck.slice(0, i + 1), { ...EMPTY, background: deck[i].background }, ...deck.slice(i + 1)], i + 1), <><Plus size={15} /> {t("sl_add")}</>)}
          {action(t("sl_up"), () => swap(i - 1), <ArrowUp size={16} />, i === 0)}
          {action(t("sl_down"), () => swap(i + 1), <ArrowDown size={16} />, i === deck.length - 1)}
          {action(t("sl_delete"), () => arrange(deck.filter((_, n) => n !== i), Math.max(i - 1, 0)), <Trash2 size={15} />, !slides.length)}
        </Stage>
      </div>
    );
  });
