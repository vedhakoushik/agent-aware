import type { Insights } from "../../lib/types";

/** AI insights: summary, per-platform badges, value-score bars. */
export default function InsightsPanel({ insights }: { insights: Insights }) {
  if (!insights?.available) {
    return (
      <div className="rounded-xl border border-dashed border-line p-4 text-sm text-faint">
        No AI insights for this run.
      </div>
    );
  }

  const badges = insights.badges ?? {};
  const scores = insights.value_scores ?? {};

  return (
    <div>
      {typeof insights.summary === "string" && insights.summary && (
        <div className="panel p-4">
          <div className="cap pb-1">AI summary</div>
          <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink2">
            {insights.summary}
          </p>
        </div>
      )}

      {Object.keys(badges).length > 0 && (
        <>
          <div className="cap mt-3 pb-1.5">Platform badges</div>
          {Object.entries(badges).map(([platform, list]) => (
            <div key={platform} className="mb-1.5 flex flex-wrap items-center gap-1.5">
              <span className="w-28 truncate text-xs font-semibold text-ink2">{platform}</span>
              {(list ?? []).map((b) => (
                <span key={b} className="chip !border-gold/40 !bg-gold/10 !text-choco text-[10.5px]">
                  {b}
                </span>
              ))}
            </div>
          ))}
        </>
      )}

      {Object.keys(scores).length > 0 && (
        <>
          <div className="cap mt-3 pb-1.5">Value scores</div>
          {Object.entries(scores).map(([platform, score]) => {
            const pct = Math.max(0, Math.min(100, Number(score) || 0));
            return (
              <div key={platform} className="mb-1.5 flex items-center gap-2">
                <span className="w-28 truncate text-xs text-ink2">{platform}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-warm">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-orange to-flame"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="w-8 text-right font-mono text-[10.5px] text-faint">
                  {Math.round(pct)}
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
