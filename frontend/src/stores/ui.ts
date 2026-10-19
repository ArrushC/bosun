import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { TaskStatus } from "@/api/types";
import type { HiddenKind } from "@/lib/graph/reduce";

export type Theme = "dark" | "light";
export type GroupBy = "none" | "priority" | "workspace" | "kind";
export type SortBy = "manual" | "priority" | "updated" | "created";

interface BoardFilters {
  q: string;
  labels: string[];
  priorities: string[];
  workspaceId: string | null;
  sources: string[];
  agentState: "any" | "running" | "attention";
  groupBy: GroupBy;
  sortBy: SortBy;
  showDone: boolean;
}

interface UiState {
  theme: Theme;
  railCollapsed: boolean;
  board: BoardFilters;
  graph: { direction: "LR" | "TB"; hidden: HiddenKind[]; aggregate: boolean; minimap: boolean; recorderOpen: boolean };
  paletteOpen: boolean;
  createOpen: boolean;
  createStatus: TaskStatus | null;
  setTheme: (t: Theme) => void;
  toggleRail: () => void;
  setBoard: (patch: Partial<BoardFilters>) => void;
  resetBoard: () => void;
  setGraph: (patch: Partial<UiState["graph"]>) => void;
  setPalette: (open: boolean) => void;
  setCreate: (open: boolean, status?: TaskStatus) => void;
}

const DEFAULT_BOARD: BoardFilters = {
  q: "",
  labels: [],
  priorities: [],
  workspaceId: null,
  sources: [],
  agentState: "any",
  groupBy: "none",
  sortBy: "manual",
  showDone: true,
};

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: "dark",
      railCollapsed: false,
      board: DEFAULT_BOARD,
      graph: { direction: "LR", hidden: [], aggregate: true, minimap: true, recorderOpen: true },
      paletteOpen: false,
      createOpen: false,
      createStatus: null,
      setTheme: (theme) => {
        document.documentElement.dataset.theme = theme;
        set({ theme });
      },
      toggleRail: () => set((s) => ({ railCollapsed: !s.railCollapsed })),
      setBoard: (patch) => set((s) => ({ board: { ...s.board, ...patch } })),
      resetBoard: () => set((s) => ({ board: { ...DEFAULT_BOARD, groupBy: s.board.groupBy, sortBy: s.board.sortBy } })),
      setGraph: (patch) => set((s) => ({ graph: { ...s.graph, ...patch } })),
      setPalette: (paletteOpen) => set({ paletteOpen }),
      setCreate: (createOpen, status) => set({ createOpen, createStatus: status ?? null }),
    }),
    {
      name: "bosun-ui",
      partialize: (s) => ({ theme: s.theme, railCollapsed: s.railCollapsed, board: s.board, graph: s.graph }),
      onRehydrateStorage: () => (state) => {
        if (state) document.documentElement.dataset.theme = state.theme;
      },
    },
  ),
);
