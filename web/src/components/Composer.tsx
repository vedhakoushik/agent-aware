import { useRef, useState } from "react";
import { Search, Send, Square } from "lucide-react";

/** Bottom composer: auto-growing textarea, Enter to send, Stop while running. */
export default function Composer({
  onSubmit,
  busy,
  running,
  hasResult,
  onCancel,
}: {
  onSubmit: (text: string) => void;
  busy: boolean;
  running: boolean;
  hasResult: boolean;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  const resize = () => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 150) + "px";
  };

  const submit = () => {
    const t = value.trim();
    if (!t || busy) return;
    onSubmit(t);
    setValue("");
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
    });
  };

  return (
    <div className="bg-cream px-5 pb-5 pt-2">
      <div className="mx-auto w-full max-w-4xl">
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-card p-2 pl-4 shadow-card transition-all focus-within:border-orange/40 focus-within:shadow-glow">
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            disabled={busy}
            onChange={(e) => {
              setValue(e.target.value);
              resize();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={
              hasResult
                ? 'Ask a follow-up… e.g. "show only non-stop", "cheapest under 9000"'
                : 'Try: "flights Delhi to Goa this Friday under ₹5000"'
            }
            className="flex-1 resize-none bg-transparent py-2.5 text-sm outline-none placeholder:text-faint disabled:opacity-60"
          />
          {running && (
            <button
              onClick={onCancel}
              className="flex items-center gap-1.5 rounded-xl border border-bad/30 px-3 py-2.5 text-xs font-semibold text-bad transition-colors hover:bg-bad/5"
            >
              <Square size={12} /> Stop
            </button>
          )}
          <button
            onClick={submit}
            disabled={busy || !value.trim()}
            className="btn-primary !px-4 !py-2.5"
          >
            {hasResult ? "Send" : "Search"} <Send size={14} />
          </button>
        </div>
        <div className="flex items-center justify-center gap-1.5 pt-2 text-[11px] text-faint">
          <Search size={11} />
          Searches across 20+ platforms simultaneously · Press Enter to send
        </div>
      </div>
    </div>
  );
}
