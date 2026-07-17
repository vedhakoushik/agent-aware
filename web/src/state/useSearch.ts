import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { bestPickLine } from "../lib/format";
import type { LiveSnapshot, RecentSearch, SearchResult, ThreadItem } from "../lib/types";

/**
 * Single source of truth for the chat page:
 *  - startSearch(): POST /search/start, then poll /search/live (streaming
 *    panels) and /search/result/{id} (completion) until the job finishes.
 *  - sendChat(): follow-up questions over the latest result; a reply may
 *    trigger a refined re-search.
 *  - recents: persisted to localStorage so the sidebar survives reloads.
 */

const RECENTS_KEY = "aa_recents_v1";
let idSeq = 0;
const nextId = () => `m${Date.now()}_${idSeq++}`;

function loadRecents(): RecentSearch[] {
  try {
    return JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]") as RecentSearch[];
  } catch {
    return [];
  }
}

export function useSearch() {
  const [items, setItems] = useState<ThreadItem[]>([]);
  const [running, setRunning] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [live, setLive] = useState<LiveSnapshot | null>(null);
  const [recents, setRecents] = useState<RecentSearch[]>(loadRecents);
  const lastResultRef = useRef<SearchResult | null>(null);
  const pollStop = useRef(false);

  useEffect(() => () => {
    pollStop.current = true; // unmount kills pollers
  }, []);

  const pushRecent = useCallback((query: string, result: SearchResult) => {
    setRecents((prev) => {
      const next: RecentSearch[] = [
        { query, summary: bestPickLine(result), at: Date.now() },
        ...prev.filter((r) => r.query !== query),
      ].slice(0, 10);
      localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const startSearch = useCallback(
    async (query: string) => {
      const q = query.trim();
      if (!q || running) return;
      setRunning(true);
      setLive(null);
      const thinkingId = nextId();
      setItems((prev) => [
        ...prev,
        { id: nextId(), role: "user", text: q },
        { id: thinkingId, role: "assistant", kind: "thinking" },
      ]);

      let jobId: string;
      try {
        ({ job_id: jobId } = await api.startSearch(q));
      } catch (e) {
        setItems((prev) =>
          prev.map((it) =>
            it.id === thinkingId
              ? { id: it.id, role: "assistant", kind: "error", text: String((e as Error).message) }
              : it,
          ),
        );
        setRunning(false);
        return;
      }

      pollStop.current = false;

      // live panel stream — cheap, frequent
      const liveTimer = window.setInterval(async () => {
        if (pollStop.current) return;
        try {
          setLive(await api.live());
        } catch {
          /* transient poll errors are fine */
        }
      }, 900);

      // completion poll
      const finish = (mut: (prev: ThreadItem[]) => ThreadItem[]) => {
        window.clearInterval(liveTimer);
        window.clearInterval(doneTimer);
        setItems(mut);
        setRunning(false);
      };
      const doneTimer = window.setInterval(async () => {
        if (pollStop.current) {
          window.clearInterval(liveTimer);
          window.clearInterval(doneTimer);
          return;
        }
        try {
          const r = await api.result(jobId);
          if (r.status === "done" && r.result) {
            lastResultRef.current = r.result;
            pushRecent(q, r.result);
            finish((prev) =>
              prev.map((it) =>
                it.id === thinkingId
                  ? { id: it.id, role: "assistant", kind: "result", result: r.result! }
                  : it,
              ),
            );
          } else if (r.status === "error") {
            finish((prev) =>
              prev.map((it) =>
                it.id === thinkingId
                  ? {
                      id: it.id,
                      role: "assistant",
                      kind: "error",
                      text: r.error ?? "Search failed — try again.",
                    }
                  : it,
              ),
            );
          }
        } catch {
          /* transient */
        }
      }, 1200);
    },
    [running, pushRecent],
  );

  const sendChat = useCallback(
    async (message: string) => {
      const m = message.trim();
      if (!m || chatBusy || running) return;
      const last = lastResultRef.current;
      setChatBusy(true);
      setItems((prev) => [...prev, { id: nextId(), role: "user", text: m }]);
      try {
        const history = items
          .filter((it): it is Extract<ThreadItem, { text: string }> => "text" in it && !!it.text)
          .map((it) => ({ role: it.role, content: it.text }));
        const reply = await api.chat({
          message: m,
          history,
          original_query: last?.query ?? "",
          platform_results: last?.platform_results ?? {},
          comparison: last?.comparison ?? {},
          recommendation: last?.recommendation ?? {},
          intent: last?.intent ?? {},
        });
        setItems((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", kind: "text", text: reply.message, source: reply.source },
        ]);
        if (reply.should_search && reply.refined_query) {
          setChatBusy(false);
          await startSearch(reply.refined_query);
          return;
        }
      } catch (e) {
        setItems((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", kind: "error", text: String((e as Error).message) },
        ]);
      }
      setChatBusy(false);
    },
    [items, chatBusy, running, startSearch],
  );

  /** Composer routes here: first message (or after New chat) = search;
   *  once a result exists, further messages are follow-up chat. */
  const submit = useCallback(
    (text: string) => {
      if (lastResultRef.current) return sendChat(text);
      return startSearch(text);
    },
    [sendChat, startSearch],
  );

  const newChat = useCallback(() => {
    pollStop.current = true;
    if (running) void api.cancel().catch(() => undefined);
    lastResultRef.current = null;
    setItems([]);
    setLive(null);
    setRunning(false);
    setChatBusy(false);
  }, [running]);

  const rerun = useCallback(
    (query: string) => {
      newChat();
      // let state settle before the new job starts
      window.setTimeout(() => void startSearch(query), 50);
    },
    [newChat, startSearch],
  );

  const cancel = useCallback(() => {
    void api.cancel().catch(() => undefined);
  }, []);

  return { items, running, chatBusy, live, recents, startSearch, sendChat, submit, newChat, rerun, cancel };
}

export type SearchStore = ReturnType<typeof useSearch>;
