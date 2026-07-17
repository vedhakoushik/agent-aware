import { useEffect, useRef } from "react";
import type { LiveSnapshot, ThreadItem } from "../lib/types";
import ThinkingCard from "./ThinkingCard";
import ResultCard from "./ResultCard";

/** The conversation column: user bubbles right, assistant surfaces left. */
export default function ChatThread({
  items,
  live,
  onSuggest,
}: {
  items: ThreadItem[];
  live: LiveSnapshot | null;
  onSuggest: (text: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const count = items.length;
  const lastKind = items[count - 1] && "kind" in items[count - 1] ? (items[count - 1] as { kind?: string }).kind : undefined;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [count, lastKind]);

  return (
    <div className="flex flex-col gap-5 py-6">
      {items.map((it) => {
        if (it.role === "user") {
          return (
            <div key={it.id} className="flex justify-end animate-rise">
              <div className="max-w-[78%] rounded-2xl rounded-br-md bg-gradient-to-r from-orange to-flame px-4 py-2.5 text-sm font-medium text-card shadow-[0_4px_14px_rgba(44,42,188,.25)]">
                {it.text}
              </div>
            </div>
          );
        }
        switch (it.kind) {
          case "thinking":
            return <ThinkingCard key={it.id} live={live} />;
          case "result":
            return <ResultCard key={it.id} result={it.result} onSuggest={onSuggest} />;
          case "error":
            return (
              <div key={it.id} className="max-w-[85%] animate-rise rounded-2xl border border-bad/25 bg-bad/5 px-4 py-3 text-sm text-bad">
                ⚠ {it.text}
              </div>
            );
          default:
            return (
              <div key={it.id} className="max-w-[85%] animate-rise">
                <div className="panel rounded-tl-md px-4 py-3 text-sm leading-relaxed text-ink2 whitespace-pre-wrap">
                  {it.text}
                </div>
                {it.source === "graph" && (
                  <div className="mt-1 pl-1 text-[0.66rem] text-faint">↳ answered from your results graph (Neo4j)</div>
                )}
              </div>
            );
        }
      })}
      <div ref={endRef} />
    </div>
  );
}
