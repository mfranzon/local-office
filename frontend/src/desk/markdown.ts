import type { JSONContent } from "@tiptap/core";
import { marked } from "marked";

/** A Desk document is stored as Markdown: that is what the assistant reads and writes, and what exports to Word.
 *  The editor works on its own tree, so this file converts between the two. */

const WORD = /[A-Za-z0-9]/;
const escapeText = (s: string) => s.replace(/([\\*`[<|])/g, "\\$1")
  .replace(/_/g, (_m, i: number, all: string) => WORD.test(all[i - 1] ?? "") && WORD.test(all[i + 1] ?? "") ? "_" : "\\_");

function inline(nodes: JSONContent[] = []): string {
  let out = "";
  for (const n of nodes) {
    if (n.type === "hardBreak") { out += "\\\n"; continue; }
    const marks = (n.marks ?? []).map((m) => m.type);
    let s = marks.includes("code") ? "`" + (n.text ?? "") + "`" : escapeText(n.text ?? "");
    const wrap = (mark: string) => {   // spaces stay outside the marks, or Markdown doesn't read them as marks
      const inner = s.trim();
      if (inner) s = s.slice(0, s.length - s.trimStart().length) + mark + inner + mark + s.slice(s.trimEnd().length);
    };
    if (marks.includes("strike")) wrap("~~");
    const stars = (marks.includes("bold") ? 2 : 0) + (marks.includes("italic") ? 1 : 0);
    if (stars) wrap("*".repeat(stars));
    const href = n.marks?.find((m) => m.type === "link")?.attrs?.href;
    out += href ? `[${s}](${href})` : s;
  }
  return out.replace(/(\\\n)+$/, "");
}

const isList = (n: JSONContent) => n.type === "bulletList" || n.type === "orderedList";

function list(n: JSONContent): string {
  return (n.content ?? []).map((item, i) => {
    const marker = n.type === "orderedList" ? `${(n.attrs?.start ?? 1) + i}. ` : "- ";
    const parts = (item.content ?? []).map((c) => isList(c) ? list(c) : block(c)).filter(Boolean);
    return marker + parts.join("\n").split("\n").join("\n" + " ".repeat(marker.length));
  }).join("\n");
}

function table(n: JSONContent): string {
  const rows = (n.content ?? []).map((row) => (row.content ?? []).map((cell) =>
    (cell.content ?? []).map((p) => inline(p.content)).filter(Boolean).join(" ").replace(/\\\n/g, " ")));
  const width = Math.max(1, ...rows.map((r) => r.length));
  const line = (r: string[]) => "| " + Array.from({ length: width }, (_, i) => r[i] ?? "").join(" | ") + " |";
  return rows.length ? [line(rows[0]), "|" + " --- |".repeat(width), ...rows.slice(1).map(line)].join("\n") : "";
}

/** One top-level node as Markdown, never with a blank line inside (except in code): blank lines separate the
 *  blocks that the assistant sees numbered. */
function block(n: JSONContent): string {
  switch (n.type) {
    case "paragraph":
      return inline(n.content).replace(/^(#{1,6}\s|>|[-+]\s)/, "\\$1").replace(/^(\d+)([.)]\s)/, "$1\\$2");
    case "heading": return "#".repeat(n.attrs?.level ?? 1) + " " + inline(n.content);
    case "bulletList": case "orderedList": return list(n);
    case "blockquote":
      return (n.content ?? []).map(block).filter(Boolean).join("\n\n").split("\n").map((l) => l ? "> " + l : ">").join("\n");
    case "codeBlock":
      return "```" + (n.attrs?.language ?? "") + "\n" + (n.content ?? []).map((c) => c.text ?? "").join("").replace(/\n$/, "") + "\n```";
    case "horizontalRule": return "---";
    case "table": return table(n);
    default: return inline(n.content);
  }
}

/** Markdown split where blank lines separate it (not inside code): the blocks the assistant sees numbered. */
export function blocks(md: string): string[] {
  const out: string[] = [];
  let cur: string[] = [], fence = false;
  for (const line of md.split("\n")) {
    if (line.trimStart().startsWith("```")) fence = !fence;
    if (line.trim() || fence) cur.push(line.trimEnd());
    else if (cur.length) { out.push(cur.join("\n")); cur = []; }
  }
  return cur.length ? [...out, cur.join("\n")] : out;
}

/** The document's top-level nodes as Markdown; an empty paragraph is "". */
export const toBlocks = (doc: JSONContent) => (doc.content ?? []).map((n) => block(n).trim());
export const toMarkdown = (doc: JSONContent) => toBlocks(doc).filter(Boolean).join("\n\n");
export const toHtml = (md: string) => marked.parse(md, { async: false, gfm: true }) as string;
