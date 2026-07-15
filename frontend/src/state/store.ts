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
  // until the user clears them.
  chatJobIds: string[] | null;
  setChatJobIds: (ids: string[] | null) => void;

  // Bumped whenever something should trigger a job-list refetch.
  refreshKey: number;
  refresh: () => void;
}

export const useStore = create<AppState>((set) => ({
  filter: { applied: "any" },
  setFilter: (partial) =>
    set((s) => ({ filter: { ...s.filter, ...partial }, chatJobIds: null })),

  chatJobIds: null,
  setChatJobIds: (ids) => set({ chatJobIds: ids }),

  refreshKey: 0,
  refresh: () => set((s) => ({ refreshKey: s.refreshKey + 1 })),
}));
