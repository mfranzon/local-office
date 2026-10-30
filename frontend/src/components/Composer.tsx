import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";
import { useI18n } from "../i18n";

export default function Composer({ placeholder, busy, onSend, onStop }: {
  placeholder: string; busy: boolean; onSend: (text: string) => void; onStop: () => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current!;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);
  const send = () => {
    if (!text.trim() || busy) return;
    onSend(text.trim());
    setText("");
  };
  return (
    <div className="flex items-end gap-2 bg-white border border-slate-300 rounded-2xl p-2 pl-4 shadow-sm focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/15">
      <textarea ref={ref} rows={1} value={text} autoFocus placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
        className="flex-1 resize-none outline-none py-2 text-[15px] bg-transparent" />
      {busy ? (
        <button onClick={onStop} title={t("stop")} className="w-9 h-9 rounded-xl bg-slate-800 text-white grid place-items-center"><Square size={14} fill="white" /></button>
      ) : (
        <button onClick={send} disabled={!text.trim()} className="w-9 h-9 rounded-xl bg-accent text-white grid place-items-center disabled:opacity-30"><ArrowUp size={18} /></button>
      )}
    </div>
  );
}
