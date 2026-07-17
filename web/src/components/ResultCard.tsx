import { useState } from "react";
import { Check, Trophy, X } from "lucide-react";
import { inr, nameOf, priceOf } from "../lib/format";
import type { SearchResult } from "../lib/types";
import AgentComms from "./AgentComms";
import ResultsTable from "./result/ResultsTable";
import InsightsPanel from "./result/InsightsPanel";
import ValidationPanel from "./result/ValidationPanel";
import EnginePanel from "./result/EnginePanel";

type Tab = "results" | "agents" | "insights" | "validation" | "engine";

const TABS: { id: Tab; label: string }[] = [
  { id: "results", label: "🏷 Results" },
  { id: "agents", label: "🛰 Agent comms" },
  { id: "insights", label: "🧠 Insights" },
  { id: "validation", label: "🛡 Validation" },
  { id: "engine", label: "🖥 Engine" },
];

const SUGGESTIONS = ["Show only the cheapest", "Any non-stop options?", "Compare top 3"];

/** The assistant's answer: best pick, KPIs, per-platform status, tabbed detail. */
export default function ResultCard({
  result,
  onSuggest,
}: {
  result: SearchResult;
  onSuggest: (text: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("results");
  const rec = result.recommendation ?? {};
  const cmp = result.comparison ?? {};
  const winnerUrl =
    typeof rec.winner_result?.url === "string" ? (rec.winner_result.url as string) : null;
  const winnerPrice = priceOf(rec.winner_result);

  return (
    <div className="flex w-full max-w-[95%] animate-rise flex-col gap-3">
      {/* best pick */}
      {rec.winner_platform && (
        <div className="panel relative overflow-hidden p-5">
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full"
            style={{ background: "radial-gradient(circle, rgba(79,70,229,.12), transparent 70%)" }}
          />
          <div className="flex flex-wrap items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-orange to-flame text-card">
              <Trophy size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="cap">Best pick — {rec.winner_platform}</div>
              <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3">
                <span className="text-lg font-bold">{nameOf(rec.winner_result)}</span>
                {winnerPrice !== null && (
                  <span className="font-display text-2xl font-bold text-orange">
                    {inr(winnerPrice)}
                  </span>
                )}
              </div>
              {rec.reasoning && (
                <p className="mt-1 text-[13px] leading-relaxed text-muted [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3] overflow-hidden">
                  {rec.reasoning}
                </p>
              )}
              {winnerUrl && (
                <a
                  href={winnerUrl}
                  target="_blank"
                  rel="noopener"
                  className="btn-primary mt-3 !px-4 !py-2 text-xs"
                >
                  Book on {rec.winner_platform} ↗
                </a>
              )}
            </div>
          </div>
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {[
          { v: String(cmp.total_results ?? 0), l: "Results", hot: false },
          {
            v: `${cmp.platforms_with_results ?? 0}/${cmp.platforms_searched ?? 0}`,
            l: "Platforms",
            hot: false,
          },
          { v: inr(cmp.overall_min_price), l: "Lowest", hot: true },
          { v: inr(cmp.overall_avg_price), l: "Average", hot: false },
        ].map((k) => (
          <div key={k.l} className="rounded-xl border border-line bg-card p-3 text-center">
            <div className={`font-display text-xl font-bold ${k.hot ? "text-orange" : ""}`}>{k.v}</div>
            <div className="cap mt-0.5">{k.l}</div>
          </div>
        ))}
      </div>

      {/* agent status pills */}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(result.platform_results).map(([pid, pr]) => {
          const found = (pr.results ?? []).length;
          return (
            <span
              key={pid}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${
                found
                  ? "border-good/30 bg-good/5 text-good"
                  : "border-line bg-warm text-faint"
              }`}
            >
              <span>{pr.icon}</span>
              {pr.platform_name}
              {found ? (
                <>
                  <Check size={12} /> {found}
                </>
              ) : (
                <X size={12} />
              )}
            </span>
          );
        })}
      </div>

      {/* tabs */}
      <div>
        <div className="flex w-fit max-w-full gap-1 overflow-x-auto rounded-xl bg-warm p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`whitespace-nowrap rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-colors ${
                tab === t.id ? "bg-card text-orange shadow-card" : "text-muted hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="mt-3">
          {tab === "results" && <ResultsTable result={result} />}
          {tab === "agents" && <AgentComms comms={result.agent_comms ?? []} />}
          {tab === "insights" && <InsightsPanel insights={result.insights ?? {}} />}
          {tab === "validation" && (
            <ValidationPanel validation={result.validation} remediation={result.remediation_log} />
          )}
          {tab === "engine" && <EnginePanel result={result} />}
        </div>
      </div>

      {/* follow-up suggestions */}
      <div className="flex flex-wrap gap-1.5 pt-1">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onSuggest(s)}
            className="chip transition-colors hover:border-orange/40 hover:text-orange"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
