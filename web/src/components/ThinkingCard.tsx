import { agentColor } from "../lib/format";
import type { LiveSnapshot } from "../lib/types";

const KIND_ICON: Record<string, string> = {
  start: "🔄",
  ok: "✅",
  warn: "⚠️",
  done: "🏁",
};

/** Live "agents working" bubble: shimmer bar, current step, agent chips,
 *  comms ticker, and the live browser screenshot when one is streaming. */
export default function ThinkingCard({ live }: { live: LiveSnapshot | null }) {
  const events = live?.events ?? [];
  const comms = live?.comms ?? [];
  const lastEvent = events[events.length - 1];
  const elapsed = lastEvent?.t !== undefined ? Math.round(Number(lastEvent.t)) : null;

  const agents = Array.from(
    new Set(comms.flatMap((c) => [c.frm, c.to]).filter((a) => a && a.toLowerCase() !== "you")),
  );
  const activeAgent = comms.length ? comms[comms.length - 1].frm : null;
  const ticker = comms.slice(-4);

  return (
    <div className="panel max-w-[92%] animate-rise overflow-hidden">
      {/* header */}
      <div className="flex items-center justify-between px-4 pt-3.5">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping2 rounded-full bg-flame opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-flame" />
          </span>
          <span className="text-sm font-semibold">Multi-agent search running</span>
        </div>
        {elapsed !== null && <span className="font-mono text-xs text-faint">{elapsed}s</span>}
      </div>

      {/* shimmer progress */}
      <div className="px-4 pt-3">
        <div className="shimmer h-1 w-full rounded-full" />
      </div>

      {/* current step */}
      {lastEvent && (
        <div className="px-4 pt-3 text-[13px] font-medium text-ink2">
          {KIND_ICON[lastEvent.kind ?? ""] ?? "•"} {lastEvent.msg ?? "Working…"}
        </div>
      )}

      {/* agent chips */}
      {agents.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-4 pt-2.5">
          {agents.map((a) => (
            <span
              key={a}
              className={`inline-flex items-center gap-1.5 rounded-full border border-line bg-cream/70 px-2.5 py-0.5 font-mono text-[10.5px] text-muted ${a === activeAgent ? "animate-pulse border-orange/40" : ""}`}
            >
              <i className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: agentColor(a) }} />
              {a}
            </span>
          ))}
        </div>
      )}

      {/* comms ticker */}
      <div className="px-4 py-3">
        {ticker.length === 0 ? (
          <div className="text-xs italic text-faint">agents spinning up…</div>
        ) : (
          ticker.map((c, i) => (
            <div
              key={`${c.t}-${c.frm}-${c.to}-${i}`}
              className={`truncate font-mono text-[11px] leading-relaxed ${i === ticker.length - 1 ? "animate-rise" : ""}`}
            >
              <b style={{ color: agentColor(c.frm) }}>{c.frm}</b>
              <span className="text-faint"> → </span>
              <b style={{ color: agentColor(c.to) }}>{c.to}</b>
              <span className="text-muted"> {c.title ?? ""}</span>
            </div>
          ))
        )}
      </div>

      {/* live browser stage */}
      {live?.screenshot && (
        <div className="mx-4 mb-4">
          <div className="cap flex items-center gap-1.5 pb-1.5">
            <i className="h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
            Live browser — {live.screenshot.platform}
          </div>
          <div className="overflow-hidden rounded-xl border border-line">
            <img
              src={`data:image/jpeg;base64,${live.screenshot.image_b64}`}
              alt={`Live view of ${live.screenshot.platform}`}
              className="block w-full"
            />
          </div>
        </div>
      )}
    </div>
  );
}
