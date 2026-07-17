import { Check, Circle, X } from "lucide-react";
import type { SearchResult } from "../../lib/types";

function StageIcon({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (["ok", "done", "pass", "passed", "success"].some((k) => s.includes(k)))
    return <Check size={14} className="text-good" />;
  if (["fail", "error", "blocked"].some((k) => s.includes(k)))
    return <X size={14} className="text-bad" />;
  return <Circle size={12} className="text-faint" />;
}

/** Under the hood: run diagnostics + browser-use telemetry. */
export default function EnginePanel({ result }: { result: SearchResult }) {
  const diag = (result.diagnostics ?? {}) as Record<string, unknown>;
  const runs = result.browser_runs ?? [];
  const stages = Array.isArray(diag.stages) ? (diag.stages as Record<string, unknown>[]) : null;
  const scalarEntries = Object.entries(diag).filter(
    ([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v),
  );
  const nestedEntries = Object.entries(diag).filter(
    ([k, v]) => k !== "stages" && v !== null && typeof v === "object",
  );
  const hasAnything = Object.keys(diag).length > 0 || runs.length > 0;

  if (!hasAnything) {
    return (
      <div className="rounded-xl border border-dashed border-line p-4 text-sm text-faint">
        Engine telemetry appears here after a search.
      </div>
    );
  }

  return (
    <div>
      {Object.keys(diag).length > 0 && (
        <>
          <div className="cap pb-1.5">Run diagnostics</div>
          {stages &&
            stages.map((st, i) => (
              <div key={i} className="flex items-center gap-2 px-1 py-1.5">
                <StageIcon status={String(st.status ?? "")} />
                <span className="text-[13px] font-medium text-ink2">
                  {String(st.name ?? st.label ?? `Stage ${i + 1}`)}
                </span>
                {typeof st.detail === "string" && st.detail && (
                  <span className="truncate text-[11.5px] text-faint">{st.detail}</span>
                )}
                <span className="ml-auto font-mono text-[11px] text-faint">
                  {st.elapsed !== undefined
                    ? `${Number(st.elapsed).toFixed(1)}s`
                    : st.seconds !== undefined
                      ? `${Number(st.seconds).toFixed(1)}s`
                      : ""}
                </span>
              </div>
            ))}
          {scalarEntries.length > 0 && (
            <div className="mt-1 grid grid-cols-1 gap-x-4 md:grid-cols-2">
              {scalarEntries.map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between border-b border-line/60 px-1 py-1.5">
                  <span className="cap">{k.replace(/_/g, " ")}</span>
                  <span className="font-mono text-[12px] text-ink2">{String(v)}</span>
                </div>
              ))}
            </div>
          )}
          {nestedEntries.map(([k, v]) => (
            <details key={k} className="mt-1.5">
              <summary className="cursor-pointer text-[11px] font-medium text-orange">{k}</summary>
              <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-warm/60 p-2.5 font-mono text-[11px] text-ink2">
                {JSON.stringify(v, null, 1)}
              </pre>
            </details>
          ))}
        </>
      )}

      {runs.length > 0 && (
        <>
          <div className="cap mt-4 pb-1.5">Browser-use runs</div>
          {runs.map((run, i) => {
            const name = String(run.platform_name ?? run.platform_id ?? "browser run");
            const status = run.status !== undefined ? String(run.status) : null;
            const steps = Array.isArray(run.steps) ? (run.steps as unknown[]) : [];
            const roadblock = run.roadblock;
            const good = status && ["ok", "done", "success"].some((k) => status.toLowerCase().includes(k));
            const bad = status && ["blocked", "error", "fail"].some((k) => status.toLowerCase().includes(k));
            return (
              <div key={i} className="mb-2 rounded-xl border border-line p-3">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold">{name}</span>
                  {status && (
                    <span
                      className={`chip !px-2 !py-0 text-[10.5px] ${
                        good
                          ? "!border-good/30 !bg-good/5 !text-good"
                          : bad
                            ? "!border-bad/30 !bg-bad/5 !text-bad"
                            : ""
                      }`}
                    >
                      {status}
                    </span>
                  )}
                </div>
                {steps.slice(0, 6).map((s, j) => (
                  <div key={j} className="mt-1 truncate font-mono text-[11px] text-muted">
                    › {typeof s === "string" ? s : JSON.stringify(s)}
                  </div>
                ))}
                {roadblock !== undefined && roadblock !== null && (
                  <div className="mt-1.5 rounded-lg border border-warn/30 bg-warn/5 px-2.5 py-1.5 text-[11.5px] text-warn">
                    {typeof roadblock === "string" ? roadblock : JSON.stringify(roadblock)}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
