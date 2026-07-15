export interface JobRow {
  job_id: string;
  title: string;
  company?: string | null;
  location?: string | null;
  job_url?: string | null;
  relevance_score?: number | null;
  resume_pdf?: string | null;
  resume_available: boolean;
  run_id?: string | null;
  tuner_status?: string | null;
  applied: boolean;
  applied_status?: string | null;
}

export interface RunsResponse {
  run_ids: string[];
  latest: Record<string, string | null>;
}

export type ConfigPhase = "scraper" | "matcher" | "funnel" | "tuner" | "applier";

export interface ConfigResponse {
  phase: string;
  values: Record<string, unknown>;
  docs: Record<string, string>;
  types: Record<string, string>;
  options: Record<string, string[]>;
}

export interface RunState {
  phase: string;
  running: boolean;
  pid?: number | null;
  started_at?: string | null;
  last_exit?: number | null;
  argv?: string[] | null;
}

export interface ChatJob {
  job_id: string;
  title: string;
  company?: string;
  score?: number;
  applied?: boolean;
  resume?: boolean;
  url?: string;
}

export interface RunProposal {
  action: "run" | "stop";
  phase: string;
  flags: Record<string, unknown>;
}
