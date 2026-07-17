import { agentColor } from "../lib/format";
import type { CommsEvent } from "../lib/types";

/** Full glass-box feed: every message agents sent each other, payloads included. */
export default function AgentComms({ comms }: { comms: CommsEvent[] }) {
  if (!comms.length) {
    return (
      <div className="rounded-xl border border-dashed border-line p-4 text-sm text-faint">
        Run a search — every message agents send each other appears here: the plan, the dispatches
        to each website agent, the results they send back, and the monitor's diagnosis when a tab
        gets stuck.
      </div>
    );
  }
  return (
    <div className="max-h-[420px] overflow-y-auto pr-1">
      {comms.map((c, i) => {
        const fc = agentColor(c.frm);
        const tc = agentColor(c.to);
        return (
          <div
            key={`${c.t}-${i}`}
            className="mb-2 rounded-xl border border-line bg-cream/60 px-3.5 py-2.5"
            style={{ borderLeft: `3px solid ${fc}` }}
          >
            <div className="flex items-center gap-2 font-mono text-xs">
              <b style={{ color: fc }}>{c.frm}</b>
              <span className="text-faint">→</span>
              <b style={{ color: tc }}>{c.to}</b>
              {c.kind && (
                <span
                  className="rounded-full px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-wide"
                  style={{ backgroundColor: fc + "1f", color: fc }}
                >
                  {c.kind}
                </span>
              )}
              {c.t !== undefined && (
                <span className="ml-auto text-[10.5px] text-faint">{c.t}s</span>
              )}
            </div>
            {c.title && <div className="mt-1 text-[13px] text-ink">{c.title}</div>}
            {c.content && (
              <details>
                <summary className="mt-1 cursor-pointer text-[11px] font-medium text-orange">
                  view payload
                </summary>
                <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-warm/60 p-2.5 font-mono text-[11px] text-ink2">
                  {c.content}
                </pre>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
