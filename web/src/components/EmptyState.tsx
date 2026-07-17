import { useEffect, useState } from "react";
import { BarChart3, Plane, UtensilsCrossed, Zap } from "lucide-react";

const EXAMPLES = [
  "best biryani near Hyderabad Secunderabad",
  "flights Delhi to Goa this Friday under ₹5000",
  "iPhone 15 best price right now",
  "budget hotels in Manali this weekend",
];

const CARDS = [
  {
    icon: Plane,
    title: "Transport Services",
    body: "Compare flights, trains, buses and ride-hailing across 50+ cities.",
    query: "flights Mumbai to Delhi tomorrow cheapest",
  },
  {
    icon: UtensilsCrossed,
    title: "Gourmet Explorer",
    body: "Deep-search menus and real-time availability for top-rated dining.",
    query: "best biryani restaurants in Hyderabad",
  },
  {
    icon: BarChart3,
    title: "Price Tracker",
    body: "Multi-agent analysis of price trends for consumer electronics.",
    query: "iPhone 15 price comparison",
  },
];

/** Hero shown before the first search — mirrors the reference layout. */
export default function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  const [exIdx, setExIdx] = useState(0);
  const [fade, setFade] = useState(true);

  useEffect(() => {
    const t = window.setInterval(() => {
      setFade(false);
      window.setTimeout(() => {
        setExIdx((i) => (i + 1) % EXAMPLES.length);
        setFade(true);
      }, 250);
    }, 3500);
    return () => window.clearInterval(t);
  }, []);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 py-16">
      <span className="chip !border-orange/25 !bg-orange/5 !text-orange">
        <Zap size={12} /> MULTI-AGENT SEARCH
      </span>

      <h1 className="text-center font-display text-4xl font-bold leading-tight md:text-5xl">
        What are you searching for?
      </h1>

      <p className="mx-auto max-w-xl text-center text-muted">
        I deploy multiple agents in parallel to compare results across platforms instantly.
      </p>

      <div className="text-sm text-faint">
        Try:{" "}
        <button
          onClick={() => onPick(EXAMPLES[exIdx])}
          className={`font-medium text-orange transition-opacity duration-200 hover:underline ${fade ? "opacity-100" : "opacity-0"}`}
        >
          “{EXAMPLES[exIdx]}”
        </button>
      </div>

      <div className="w-full pt-6">
        <div className="cap pb-3">Try these instead</div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {CARDS.map((c) => (
            <button
              key={c.title}
              onClick={() => onPick(c.query)}
              className="panel group cursor-pointer p-6 text-left transition-all hover:-translate-y-1 hover:shadow-pop"
            >
              <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-orange/10 text-orange">
                <c.icon size={20} />
              </div>
              <div className="mb-1.5 text-[15px] font-semibold">{c.title}</div>
              <div className="text-[13px] leading-relaxed text-muted">{c.body}</div>
              <div className="pt-3 text-xs font-semibold text-orange opacity-0 transition-opacity group-hover:opacity-100">
                Try this →
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
