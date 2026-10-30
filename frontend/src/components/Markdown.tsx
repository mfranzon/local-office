import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Renders an answer; [n] citation markers become clickable badges when onCite is given. */
export default function Markdown({ text, onCite }: { text: string; onCite?: (n: number) => void }) {
  const linked = onCite ? text.replace(/\[(\d{1,2})\]/g, "[$1](#cite-$1)") : text;
  return (
    <div className="prose-answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => {
          const n = href?.startsWith("#cite-") ? Number(href.slice(6)) : null;
          if (n === null) return <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">{children}</a>;
          return (
            <button onClick={() => onCite?.(n)}
              className="inline-grid place-items-center min-w-5 h-5 px-1 mx-0.5 rounded bg-accent-soft text-accent text-[11px] font-semibold align-text-top hover:bg-accent hover:text-white">
              {n}
            </button>
          );
        },
      }}>{linked}</ReactMarkdown>
    </div>
  );
}
