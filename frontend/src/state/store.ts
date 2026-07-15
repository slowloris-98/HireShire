import { create } from "zustand";

export interface JobFilter {
  run_id?: string;
  min_score?: number;
  applied?: "any" | "yes" | "no";
  q?: string;
}

interface AppState {
  filter: JobFilter;
  setFilter: (partial: Partial<JobFilter>) => void;

  // When the chat surfaces a specific set of jobs, they take over the panel
  // until the user clears them. chatRunId is the run scope the chat tool
  // resolved the ids against; the panel must re-fetch with that same scope,
  // since /api/jobs ANDs run_id with job_ids.
  chatJobIds: string[] | null;
  chatRunId: string | null;
  setChatResults: (ids: string[] | null, runId?: string | null) => void;

  // Bumped whenever something should trigger a job-list refetch.
  refreshKey: number;
  refresh: () => void;
}

export const useStore = create<AppState>((set) => ({
  filter: { applied: "any" },
  setFilter: (partial) =>
    set((s) => ({ filter: { ...s.filter, ...partial }, chatJobIds: null, chatRunId: null })),

  chatJobIds: null,
  chatRunId: null,
  setChatResults: (ids, runId = null) => set({ chatJobIds: ids, chatRunId: runId }),

  refreshKey: 0,
  refresh: () => set((s) => ({ refreshKey: s.refreshKey + 1 })),
}));
