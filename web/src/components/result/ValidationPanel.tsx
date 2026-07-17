import { Check, X } from "lucide-react";
import type { Validation } from "../../lib/types";

const VERDICTS: Record<string, { icon: string; label: string; color: string }> = {
  valid: { icon: "✅", label: "Validated", color: "#1E7F4F" },
  fixed: { icon: "🛠️", label: "Auto-fixed", color: "#C05800" },
  best_effort: { icon: "⚖️", label: "Best available", color: "#B45309" },
  issues_remain: { icon: "⚠️", label: "Issues flagged", color: "#C0392B" },
};

/** The autonomous critic's audit: verdict, checks with evidence, fixes, rounds. */
export default function ValidationPanel({
  validation,
  remediation,
}: {
  validation?: Validation | null;
  remediation?: Record<string, unknown>[] | null;
}) {
  if (!validation || Object.keys(validation).length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line p-4 text-sm text-faint">
        No validation data for this run.
      </div>
    );
  }

  const v = VERDICTS[validation.verdict ?? ""] ?? {
    icon: "•",
    label: validation.verdict ?? "Unknown",
    color: "#6B5338",
  };
  const checks = validation.checks ?? [];
  const fixes = validation.fixes ?? [];
  const notes = validation.notes ?? [];

  return (
    <div>
      <div
        className="flex items-center gap-2.5 rounded-xl border px-4 py-3"
        style={{ backgroundColor: v.color + "0d", borderColor: v.color + "40" }}
      >
        <span className="text-lg">{v.icon}</span>
        <span className="text-sm font-bold" style={{ color: v.color }}>
          {v.label}
        </span>
        {notes.length > 0 && (
          <span className="chip !px-2 !py-0 text-[10.5px]">{notes.length} note{notes.length > 1 ? "s" : ""}</span>
        )}
      </div>

      {checks.length > 0 && (
        <div className="mt-3">
          {checks.map((c, i) => (
            <div key={i} className="flex items-start gap-2 px-1 py-1.5">
              {c.passed ? (
                <Check size={15} className="mt-0.5 shrink-0 text-good" />
              ) : (
                <X size={15} className="mt-0.5 shrink-0 text-bad" />
              )}
              <div className="min-w-0">
                <div className="text-[13px] font-medium text-ink2">{c.name ?? `Check ${i + 1}`}</div>
                {typeof c.evidence === "string" && c.evidence && (
                  <div className="mt-0.5 break-words font-mono text-[11.5px] text-faint">
                    {c.evidence}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {notes.length > 0 && (
        <div className="mt-3">
          <div className="cap pb-1">Notes</div>
          {notes.map((n, i) => (
            <div key={i} className="mb-1 rounded-lg border border-warn/30 bg-warn/5 px-2.5 py-1.5 text-[11.5px] text-warn">
              {String(n)}
            </div>
          ))}
        </div>
      )}

      {fixes.length > 0 && (
        <div className="mt-3">
          <div className="cap pb-1">Fixes applied</div>
          {fixes.map((f, i) => (
            <pre
              key={i}
              className="mb-1.5 whitespace-pre-wrap rounded-lg border border-line bg-warm/60 p-2.5 font-mono text-[11px] text-ink2"
            >
              {JSON.stringify(f, null, 1)}
            </pre>
          ))}
        </div>
      )}

      {remediation && remediation.length > 0 && (
        <div className="mt-3">
          <div className="cap pb-1.5">Remediation rounds</div>
          {remediation.map((r, i) => (
            <div key={i} className="mb-1.5 flex items-center gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-orange/10 text-[10px] font-bold text-orange">
                {i + 1}
              </span>
              <span className="truncate font-mono text-[11px] text-muted">
                {JSON.stringify(r).slice(0, 140)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
