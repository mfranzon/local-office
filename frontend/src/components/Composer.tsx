import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square, Wand2 } from "lucide-react";
import { api, type SkillInfo } from "../api";
import { useI18n } from "../i18n";

/** Message box. Typing "/" at the start lists the enabled skills; picking one inserts "/name ". */
export default function Composer({ placeholder, busy, onSend, onStop, seed, skills = false }: {
  placeholder: string; busy: boolean; onSend: (text: string) => void; onStop: () => void;
  seed?: { text: string }; skills?: boolean;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [available, setAvailable] = useState<SkillInfo[]>([]);
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current!;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);
  useEffect(() => { if (seed) { setText(seed.text); ref.current?.focus(); } }, [seed]);

  const typing = skills ? /^\/([a-z0-9-]*)$/.exec(text) : null;   // still writing the skill name
  useEffect(() => {
    if (typing && text === "/") api.skills().then((all) => setAvailable(all.filter((s) => s.enabled))).catch(() => {});
  }, [typing, text]);
  const matches = typing ? available.filter((s) => s.name.includes(typing[1])) : [];
  useEffect(() => setActive(0), [matches.length]);

  const pick = (s: SkillInfo) => { setText(`/${s.name} `); ref.current?.focus(); };
  const send = () => {
    if (!text.trim() || busy) return;
    onSend(text.trim());
    setText("");
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (matches.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => (a + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(matches[active]); return; }
      if (e.key === "Escape") { setText(""); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
  };

  return (
    <div className="relative">
      {matches.length > 0 && (
        <ul className="absolute bottom-full mb-2 left-0 right-0 bg-white border border-slate-200 rounded-xl shadow-lg py-1.5 max-h-72 overflow-auto">
          {matches.map((s, i) => (
            <li key={s.name}>
              <button onMouseDown={(e) => { e.preventDefault(); pick(s); }} onMouseEnter={() => setActive(i)}
                className={`w-full text-left flex gap-3 px-3 py-2 ${i === active ? "bg-accent-soft" : ""}`}>
                <Wand2 size={16} className="shrink-0 mt-0.5 text-violet-600" />
                <span className="min-w-0">
                  <span className="block font-mono text-sm font-medium">/{s.name}</span>
                  <span className="block text-xs text-slate-500 truncate">{s.description}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-end gap-2 bg-white border border-slate-300 rounded-2xl p-2 pl-4 shadow-sm focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/15">
        <textarea ref={ref} rows={1} value={text} autoFocus placeholder={placeholder}
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          className="flex-1 resize-none outline-none py-2 text-[15px] bg-transparent" />
        {busy ? (
          <button onClick={onStop} title={t("stop")} className="w-9 h-9 rounded-xl bg-slate-800 text-white grid place-items-center"><Square size={14} fill="white" /></button>
        ) : (
          <button onClick={send} disabled={!text.trim()} className="w-9 h-9 rounded-xl bg-accent text-white grid place-items-center disabled:opacity-30"><ArrowUp size={18} /></button>
        )}
      </div>
    </div>
  );
}
