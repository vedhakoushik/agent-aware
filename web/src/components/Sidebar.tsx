import { MessageSquare, Plus, Sparkles, User } from "lucide-react";
import type { RecentSearch } from "../lib/types";

/** Left rail: brand, new chat, session history, account footer. */
export default function Sidebar({
  recents,
  onNew,
  onPick,
  busy,
}: {
  recents: RecentSearch[];
  onNew: () => void;
  onPick: (q: string) => void;
  busy: boolean;
}) {
  return (
    <aside className="flex h-full w-[290px] shrink-0 flex-col border-r border-line bg-card">
      {/* brand */}
      <div className="flex items-center gap-2.5 px-5 pb-4 pt-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-orange to-flame text-card shadow-[0_4px_12px_rgba(44,42,188,.35)]">
          <Sparkles size={17} />
        </div>
        <span className="font-display text-[15px] font-bold tracking-tight">Agent-Aware</span>
      </div>

      {/* new chat */}
      <div className="px-4">
        <button onClick={onNew} className="btn-primary w-full">
          <Plus size={16} /> New chat
        </button>
      </div>

      {/* recents */}
      <div className="cap px-5 pb-1 pt-5">Recent</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2.5">
        {recents.length === 0 && (
          <div className="px-2.5 py-1 text-xs text-faint">Your searches will appear here.</div>
        )}
        {recents.map((r) => (
          <button
            key={r.query + r.at}
            onClick={() => onPick(r.query)}
            disabled={busy}
            className="group flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition-colors hover:bg-warm disabled:opacity-50"
          >
            <MessageSquare size={14} className="mt-0.5 shrink-0 text-faint group-hover:text-orange" />
            <span className="min-w-0">
              <span className="block truncate text-[13px] font-medium text-ink2">{r.query}</span>
              <span className="block truncate text-[11px] text-faint">{r.summary}</span>
            </span>
          </button>
        ))}
      </div>

      {/* footer */}
      <div className="border-t border-line px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-warm text-muted">
            <User size={14} />
          </div>
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-ink2">You</div>
            <div className="text-[11px] text-faint">Local · no sign-in</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
