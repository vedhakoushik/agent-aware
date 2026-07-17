import { inr, nameOf, priceOf } from "../../lib/format";
import type { SearchResult } from "../../lib/types";

const META_FIELDS = ["airline", "stops", "duration", "rating", "location", "seller"] as const;

function metaLine(r: Record<string, unknown>): string {
  return META_FIELDS.map((f) => r[f])
    .filter((v) => v !== undefined && v !== null && String(v).trim() !== "")
    .map(String)
    .join(" · ");
}

/** Per-platform results browser (top 5 per platform, winner flagged). */
export default function ResultsTable({ result }: { result: SearchResult }) {
  const entries = Object.entries(result.platform_results ?? {});
  const withResults = entries.filter(([, pr]) => (pr.results ?? []).length > 0);
  const empty = entries.filter(([, pr]) => (pr.results ?? []).length === 0);
  const compareType = result.comparison?.compare_type;

  return (
    <div>
      {compareType && (
        <div className="mb-3 text-[12.5px] text-muted">
          ⚖️ Comparing <b>{compareType}</b> like-for-like across every platform.
        </div>
      )}

      {withResults.map(([pid, pr]) => {
        const rows = pr.results ?? [];
        const isWinner = pid === result.recommendation?.winner_platform ||
          pr.platform_name === result.recommendation?.winner_platform;
        return (
          <div key={pid} className="panel mb-3 overflow-hidden">
            <div className="flex items-center gap-2 border-b border-line bg-warm/50 px-4 py-2.5">
              <span>{pr.icon}</span>
              <span className="text-sm font-semibold">{pr.platform_name}</span>
              <span className="chip !px-2 !py-0 text-[10.5px]">{rows.length}</span>
              {isWinner && (
                <span className="chip !border-good/25 !bg-good/10 !px-2 !py-0 text-[10.5px] font-bold !text-good">
                  WINNER
                </span>
              )}
              <span className="ml-auto font-mono text-[11px] text-faint">
                {Number(pr.elapsed_seconds ?? 0).toFixed(1)}s
              </span>
            </div>
            {rows.slice(0, 5).map((r, i) => {
              const url = typeof r.url === "string" ? r.url : null;
              const inner = (
                <>
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-medium">{nameOf(r)}</span>
                    {metaLine(r) && (
                      <span className="block truncate text-[11px] text-faint">{metaLine(r)}</span>
                    )}
                  </span>
                  <span className="whitespace-nowrap text-sm font-semibold">{inr(priceOf(r))}</span>
                </>
              );
              const cls =
                "flex items-center justify-between gap-3 border-b border-line/60 px-4 py-2 last:border-0 hover:bg-cream/70 transition-colors";
              return url ? (
                <a key={i} href={url} target="_blank" rel="noopener" className={cls}>
                  {inner}
                </a>
              ) : (
                <div key={i} className={cls}>
                  {inner}
                </div>
              );
            })}
            {rows.length > 5 && (
              <div className="px-4 py-1.5 text-[11px] text-faint">
                +{rows.length - 5} more on {pr.platform_name}
              </div>
            )}
          </div>
        );
      })}

      {empty.map(([pid, pr]) => (
        <div
          key={pid}
          className="mb-2 rounded-xl border border-dashed border-line px-4 py-2.5 text-[12.5px] text-faint"
        >
          {pr.icon} {pr.platform_name} — no results
          {pr.error ? ` · ${String(pr.error).slice(0, 90)}` : ""}
        </div>
      ))}

      {withResults.length === 0 && empty.length === 0 && (
        <div className="rounded-xl border border-dashed border-line p-4 text-sm text-faint">
          No platform results in this run.
        </div>
      )}
    </div>
  );
}
