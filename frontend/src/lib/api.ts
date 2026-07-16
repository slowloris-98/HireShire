import type { ConfigResponse, JobRow, RunState, RunsResponse } from "./types";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  runs: () => fetch("/api/runs").then((r) => j<RunsResponse>(r)),

  jobs: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "" && v !== null) q.set(k, String(v));
    }
    return fetch(`/api/jobs?${q.toString()}`).then((r) => j<JobRow[]>(r));
  },

  getConfig: (phase: string) =>
    fetch(`/api/config/${phase}`).then((r) => j<ConfigResponse>(r)),

  putConfig: (phase: string, values: Record<string, unknown>) =>
    fetch(`/api/config/${phase}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    }).then((r) => j<{ phase: string; saved: string[]; values: Record<string, unknown> }>(r)),

  runStatus: () => fetch("/api/runs/status").then((r) => j<RunState[]>(r)),

  startRun: (phase: string, flags: Record<string, unknown>) =>
    fetch(`/api/runs/${phase}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flags }),
    }).then((r) => j<RunState>(r)),

  stopRun: (phase: string) =>
    fetch(`/api/runs/${phase}/stop`, { method: "POST" }).then((r) => j<RunState>(r)),

  resumeUrl: (runId: string, jobId: string) => `/api/resume/${runId}/${jobId}`,
};
