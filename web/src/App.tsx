import { useState } from "react";
import { Menu, Zap } from "lucide-react";
import Sidebar from "./components/Sidebar";
import EmptyState from "./components/EmptyState";
import Composer from "./components/Composer";
import ChatThread from "./components/ChatThread";
import Landing from "./pages/Landing";
import { useSearch } from "./state/useSearch";

/** One origin, two surfaces: "/" is the marketing landing, everything else
 *  ("/app") is the chat product. Plain-link navigation keeps it simple. */
export default function App() {
  if (window.location.pathname === "/") return <Landing />;
  return <ChatApp />;
}

function ChatApp() {
  const store = useSearch();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const busy = store.running || store.chatBusy;
  const hasThread = store.items.length > 0;

  return (
    <div className="flex h-full">
      {sidebarOpen && (
        <Sidebar
          recents={store.recents}
          onNew={store.newChat}
          onPick={store.rerun}
          busy={store.running}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        {/* top bar */}
        <header className="flex items-center justify-between border-b border-line bg-cream/80 px-5 py-3 backdrop-blur">
          <button
            aria-label="Toggle sidebar"
            onClick={() => setSidebarOpen((v) => !v)}
            className="rounded-lg p-2 text-muted transition-colors hover:bg-warm hover:text-ink"
          >
            <Menu size={18} />
          </button>
          <div className="flex items-center gap-3">
            <a
              href="/"
              className="text-xs font-medium text-faint transition-colors hover:text-orange"
            >
              ← Home
            </a>
            <span className="chip !border-orange/25 !bg-orange/5 !text-orange">
              <Zap size={12} /> MULTI-AGENT SEARCH
            </span>
          </div>
        </header>

        {/* thread / hero */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-5">
            {hasThread ? (
              <ChatThread items={store.items} live={store.live} onSuggest={store.submit} />
            ) : (
              <EmptyState onPick={(q) => void store.startSearch(q)} />
            )}
          </div>
        </div>

        {/* composer */}
        <Composer
          onSubmit={(t) => void store.submit(t)}
          busy={busy}
          running={store.running}
          hasResult={hasThread}
          onCancel={store.cancel}
        />
      </main>
    </div>
  );
}
