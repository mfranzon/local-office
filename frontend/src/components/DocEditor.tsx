import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TableKit } from "@tiptap/extension-table";
import { Bold, Italic, Link as LinkIcon, List, ListOrdered, Minus, Plus, Quote, Redo2, Strikethrough, Table, Trash2, Undo2 } from "lucide-react";
import { useI18n } from "../i18n";
import { toBlocks, toHtml, toMarkdown } from "../desk/markdown";

export interface DocSelection { start: number; end: number; text: string }
export interface DocHandle { selection: () => DocSelection | null }
/** The page while the assistant is writing an edit: the Markdown so far, and which top-level blocks are its words
 *  (from index `from`, up to the last `tail` blocks, which come after them). */
export interface Typing { md: string; from: number; tail: number }

/** Shows where the assistant is typing: its blocks are tinted and a labelled caret follows the last word. */
const typingKey = new PluginKey<{ from: number; tail: number; label: string } | null>("assistantTyping");
const AssistantTyping = Extension.create({
  name: "assistantTyping",
  addProseMirrorPlugins: () => [new Plugin({
    key: typingKey,
    state: { init: () => null, apply: (tr, value) => tr.getMeta(typingKey) === undefined ? value : tr.getMeta(typingKey) },
    props: {
      decorations(state) {
        const range = typingKey.getState(state);
        if (!range) return null;
        const decos: Decoration[] = [];
        let caret = -1;
        state.doc.forEach((node, pos, i) => {
          if (i < range.from || i >= state.doc.childCount - range.tail) return;
          decos.push(Decoration.node(pos, pos + node.nodeSize, { class: "ai-typing" }));
          if (node.isTextblock) caret = pos + node.nodeSize - 1;
          else node.descendants((n, p) => { if (n.isTextblock) caret = pos + p + n.nodeSize; });
        });
        if (caret >= 0) decos.push(Decoration.widget(caret, () => {
          const el = document.createElement("span");
          el.className = "ai-caret";
          el.dataset.label = range.label;
          return el;
        }, { side: 1, key: "ai-caret" }));
        return DecorationSet.create(state.doc, decos);
      },
    },
  })],
});

function Toolbar({ editor }: { editor: Editor }) {
  const { t } = useI18n();
  const run = () => editor.chain().focus();
  const level = [1, 2, 3].find((l) => editor.isActive("heading", { level: l })) ?? 0;
  const button = (title: string, on: boolean, act: () => void, icon: React.ReactNode, disabled = false) => (
    <button title={title} disabled={disabled} onMouseDown={(e) => e.preventDefault()} onClick={act}
      className={`h-8 min-w-8 px-1.5 rounded-md flex items-center justify-center gap-0.5 text-xs disabled:opacity-30 ${on ? "bg-accent-soft text-accent" : "text-slate-600 hover:bg-slate-100"}`}>
      {icon}
    </button>
  );
  const link = () => {
    const url = prompt(t("tb_link_prompt"), editor.getAttributes("link").href ?? "https://");
    if (url === null) return;
    if (url.trim() && url.trim() !== "https://") run().extendMarkRange("link").setLink({ href: url.trim() }).run();
    else run().extendMarkRange("link").unsetLink().run();
  };
  const sep = <span className="w-px h-5 bg-slate-200 mx-1" />;
  return (
    <div className="flex items-center gap-0.5 flex-wrap px-4 py-1.5 border-b border-slate-200 bg-white">
      {button(t("tb_undo"), false, () => run().undo().run(), <Undo2 size={16} />, !editor.can().undo())}
      {button(t("tb_redo"), false, () => run().redo().run(), <Redo2 size={16} />, !editor.can().redo())}
      {sep}
      <select value={level} title={t("tb_style")}
        onChange={(e) => { const l = Number(e.target.value); (l ? run().setHeading({ level: l as 1 | 2 | 3 }) : run().setParagraph()).run(); }}
        className="h-8 px-2 rounded-md text-sm text-slate-700 bg-transparent hover:bg-slate-100 outline-none">
        <option value={0}>{t("tb_normal")}</option>
        <option value={1}>{t("tb_h1")}</option>
        <option value={2}>{t("tb_h2")}</option>
        <option value={3}>{t("tb_h3")}</option>
      </select>
      {sep}
      {button(t("tb_bold"), editor.isActive("bold"), () => run().toggleBold().run(), <Bold size={16} />)}
      {button(t("tb_italic"), editor.isActive("italic"), () => run().toggleItalic().run(), <Italic size={16} />)}
      {button(t("tb_strike"), editor.isActive("strike"), () => run().toggleStrike().run(), <Strikethrough size={16} />)}
      {button(t("tb_link"), editor.isActive("link"), link, <LinkIcon size={16} />)}
      {sep}
      {button(t("tb_bullets"), editor.isActive("bulletList"), () => run().toggleBulletList().run(), <List size={16} />)}
      {button(t("tb_numbers"), editor.isActive("orderedList"), () => run().toggleOrderedList().run(), <ListOrdered size={16} />)}
      {button(t("tb_quote"), editor.isActive("blockquote"), () => run().toggleBlockquote().run(), <Quote size={15} />)}
      {sep}
      {button(t("tb_table"), false, () => run().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(), <Table size={16} />)}
      {editor.isActive("table") && <>
        {button(t("sh_insert_row"), false, () => run().addRowAfter().run(), <><Plus size={12} />{t("tb_row")}</>)}
        {button(t("sh_delete_row"), false, () => run().deleteRow().run(), <><Minus size={12} />{t("tb_row")}</>)}
        {button(t("sh_insert_col"), false, () => run().addColumnAfter().run(), <><Plus size={12} />{t("tb_col")}</>)}
        {button(t("sh_delete_col"), false, () => run().deleteColumn().run(), <><Minus size={12} />{t("tb_col")}</>)}
        {button(t("tb_table_del"), false, () => run().deleteTable().run(), <Trash2 size={15} />)}
      </>}
    </div>
  );
}

/** A page to write on. `content` is Markdown; it is shown again whenever `rev` changes (the assistant edited the
 *  file, or its changes were undone). What the user types comes out of onChange as Markdown. */
export default forwardRef<DocHandle, { content: string; rev: number; locked: boolean; typing: Typing | null; onChange: (md: string) => void }>(
  function DocEditor({ content, rev, locked, typing, onChange }, ref) {
    const { t } = useI18n();
    const change = useRef(onChange);
    change.current = onChange;
    const touched = useRef(false);   // the cursor says where the user is working only once they have put it somewhere
    const editor = useEditor({
      extensions: [StarterKit.configure({ underline: false, link: { openOnClick: false } }), TableKit.configure({ table: { resizable: false } }), AssistantTyping],
      content: toHtml(content),
      shouldRerenderOnTransaction: true,          // the toolbar shows what is active at the cursor
      editorProps: { attributes: { class: "desk-doc", spellcheck: "true" } },
      onUpdate: ({ editor }) => change.current(toMarkdown(editor.getJSON())),
      onFocus: () => { touched.current = true; },
    });

    const shown = useRef(rev);
    const before = useRef<string | null>(null);   // the page as it was when the assistant started typing
    useEffect(() => {
      if (rev === shown.current) return;
      shown.current = rev;
      if (before.current !== null) {              // first back to that page, so that one undo takes the whole edit back
        editor.chain().setMeta("addToHistory", false).setContent(before.current, { emitUpdate: false }).run();
        before.current = null;
      }
      editor.commands.setContent(toHtml(content), { emitUpdate: false });
    }, [rev, content, editor]);
    const label = t("desk_assistant");
    useEffect(() => {
      if (!typing) { editor.view.dispatch(editor.state.tr.setMeta(typingKey, null).setMeta("addToHistory", false)); return; }
      before.current ??= editor.getHTML();
      editor.chain().setMeta("addToHistory", false).setContent(toHtml(typing.md), { emitUpdate: false })
        .setMeta(typingKey, { from: typing.from, tail: typing.tail, label }).run();
      editor.view.dom.querySelector(".ai-caret")?.scrollIntoView({ block: "nearest" });
    }, [typing, editor, label]);
    useEffect(() => { editor.setEditable(!locked, false); }, [locked, editor]);

    useImperativeHandle(ref, () => ({
      /** Where the user is working, in the numbered blocks the assistant sees (empty paragraphs don't count). */
      selection: () => {
        const { from, to } = editor.state.selection;
        const blocks = toBlocks(editor.getJSON());
        const number = (pos: number) => blocks.slice(0, editor.state.doc.resolve(pos).index(0) + 1).filter(Boolean).length;
        const [start, end] = [number(from), number(to)];
        return start && touched.current ? { start, end, text: editor.state.doc.textBetween(from, to, "\n") } : null;
      },
    }), [editor]);

    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <Toolbar editor={editor} />
        <div className="flex-1 overflow-auto bg-slate-100" onClick={(e) => { if (e.target === e.currentTarget) editor.commands.focus("end"); }}>
          <div className={`max-w-[820px] min-h-[calc(100%-3rem)] mx-auto my-6 px-16 py-14 bg-white border border-slate-200 shadow-sm rounded-sm ${locked && !typing ? "opacity-70" : ""}`}
            onClick={(e) => { if (e.target === e.currentTarget) editor.commands.focus("end"); }}>
            <EditorContent editor={editor} />
          </div>
        </div>
      </div>
    );
  });
