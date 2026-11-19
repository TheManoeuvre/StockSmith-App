import { api } from "./client";

export type WorkspaceMode = "real" | "sample";

export interface WorkspaceState {
  mode: WorkspaceMode;
  /** False outside the installed desktop app, where nothing at start-up would act on a switch. */
  can_switch: boolean;
  sample_business: string;
}

export const workspaceApi = {
  get: () => api.get<WorkspaceState>("/workspace"),
  /** Records the switch for the next start; the caller then restarts the app to apply it. */
  switchTo: (mode: WorkspaceMode) => api.put<WorkspaceState>("/workspace", { mode }),
};
