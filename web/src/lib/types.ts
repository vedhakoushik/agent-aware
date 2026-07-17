/** API contract — mirrors api/main.py serialization of the LangGraph state. */

export interface PlatformResult {
  platform_name: string;
  icon: string;
  results: Record<string, unknown>[];
  error?: string | null;
  elapsed_seconds: number;
  tier?: string;
  roadblock?: Record<string, unknown> | null;
}

export interface Comparison {
  total_results?: number;
  platforms_with_results?: number;
  platforms_searched?: number;
  overall_min_price?: number | null;
  overall_avg_price?: number | null;
  compare_type?: string;
  ranked_platforms?: {
    platform_id?: string;
    platform_name: string;
    type_matched?: boolean;
    [k: string]: unknown;
  }[];
  [k: string]: unknown;
}

export interface Recommendation {
  winner_platform?: string;
  winner_result?: Record<string, unknown>;
  reasoning?: string;
  confidence?: string;
  [k: string]: unknown;
}

export interface Insights {
  available?: boolean;
  badges?: Record<string, string[]>;
  value_scores?: Record<string, number>;
  summary?: string;
  matrix?: unknown;
  [k: string]: unknown;
}

export interface Validation {
  verdict?: string; // valid | fixed | best_effort | issues_remain
  checks?: { name?: string; passed?: boolean; evidence?: string; [k: string]: unknown }[];
  fixes?: Record<string, unknown>[];
  notes?: string[];
  [k: string]: unknown;
}

export interface Intent {
  type?: string;
  params?: Record<string, unknown>;
  clarification_needed?: boolean;
  clarification_question?: string;
  [k: string]: unknown;
}

export interface SearchResult {
  query: string;
  status: string;
  intent?: Intent | null;
  platform_results: Record<string, PlatformResult>;
  comparison?: Comparison | null;
  segments?: Record<string, unknown> | null;
  insights?: Insights | null;
  recommendation?: Recommendation | null;
  diagnostics?: Record<string, unknown> | null;
  browser_runs?: Record<string, unknown>[] | null;
  agent_comms?: CommsEvent[] | null;
  validation?: Validation | null;
  remediation_log?: Record<string, unknown>[] | null;
  error?: string | null;
}

/** One message on the agent-communication bus. */
export interface CommsEvent {
  frm: string;
  to: string;
  kind?: string; // message | request | result | diagnosis | plan …
  title?: string;
  content?: string;
  t?: number; // seconds since run start
  [k: string]: unknown;
}

export interface ProgressEvent {
  kind?: string; // start | ok | warn | done | info
  msg?: string;
  t?: number;
  [k: string]: unknown;
}

export interface LiveSnapshot {
  running: boolean;
  events: ProgressEvent[];
  comms: CommsEvent[];
  diagnostics: Record<string, unknown>;
  browser_runs: Record<string, unknown>[];
  screenshot?: { platform: string; image_b64: string } | null;
}

export interface ChatReply {
  message: string;
  source: "graph" | "llm";
  should_search: boolean;
  refined_query: string | null;
}

/** Chat-thread item, either side. */
export type ThreadItem =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; kind: "thinking" }
  | { id: string; role: "assistant"; kind: "result"; result: SearchResult }
  | { id: string; role: "assistant"; kind: "text"; text: string; source?: string }
  | { id: string; role: "assistant"; kind: "error"; text: string };

export interface RecentSearch {
  query: string;
  summary: string;
  at: number;
}
