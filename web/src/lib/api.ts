import type { ChatReply, LiveSnapshot, SearchResult } from "./types";

/** Thin client over the FastAPI backend (proxied at /api by Vite). */

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  startSearch: (query: string) =>
    fetch("/api/search/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    }).then((r) => j<{ job_id: string }>(r)),

  live: () => fetch("/api/search/live").then((r) => j<LiveSnapshot>(r)),

  result: (jobId: string) =>
    fetch(`/api/search/result/${jobId}`).then((r) =>
      j<{ status: string; query: string; result: SearchResult | null; error: string | null }>(r),
    ),

  cancel: () => fetch("/api/search/cancel", { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),

  chat: (payload: {
    message: string;
    history: { role: string; content: string }[];
    original_query: string;
    platform_results: Record<string, unknown>;
    comparison: Record<string, unknown>;
    recommendation: Record<string, unknown>;
    intent: Record<string, unknown>;
  }) =>
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<ChatReply>(r)),

  graphAvailable: () =>
    fetch("/api/graph/available").then((r) => j<{ available: boolean }>(r)),

  graphCypher: (query: string) =>
    fetch("/api/graph/cypher", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    }).then((r) => j<{ ok: boolean; rows?: Record<string, unknown>[]; columns?: string[]; error?: string }>(r)),

  graphAsk: (question: string) =>
    fetch("/api/graph/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }).then((r) =>
      j<{ ok: boolean; cypher?: string; rows?: Record<string, unknown>[]; error?: string }>(r),
    ),
};
