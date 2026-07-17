import type { Recommendation, SearchResult } from "./types";

/** Display helpers shared across panels. */

const PRICE_FIELDS = ["price", "price_per_night", "price_per_day", "total_price"] as const;

export function priceOf(r: Record<string, unknown> | undefined | null): number | null {
  if (!r) return null;
  for (const f of PRICE_FIELDS) {
    const v = r[f];
    if (v === undefined || v === null || v === "") continue;
    const n = Number(String(v).replace(/[,₹\s]/g, ""));
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

export function nameOf(r: Record<string, unknown> | undefined | null, fallback = "—"): string {
  if (!r) return fallback;
  for (const f of ["name", "title", "airline", "hotel_name", "product_name"]) {
    const v = r[f];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return fallback;
}

export function bestPickLine(result: SearchResult): string {
  const rec: Recommendation = result.recommendation ?? {};
  const total = result.comparison?.total_results ?? 0;
  if (rec.winner_platform) {
    const p = priceOf(rec.winner_result);
    return `Found ${total} options · best ${p ? inr(p) : "pick"} on ${rec.winner_platform}`;
  }
  return total ? `Found ${total} results` : "Search complete";
}

export function agentColor(name: string): string {
  const palette = ["#2C2ABC", "#4F46E5", "#6366F1", "#3730A3", "#7C3AED", "#0E7490"];
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 997;
  return palette[h % palette.length];
}

export function ago(ts: number): string {
  const s = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}
