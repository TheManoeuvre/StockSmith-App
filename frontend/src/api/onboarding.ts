import { api } from "./client";

export type SetupStepKey =
  | "business_type"
  | "materials"
  | "opening_stock"
  | "kitting"
  | "products"
  | "orders"
  | "offsite_backup";

export interface SetupStep {
  key: SetupStepKey;
  done: boolean;
  optional: boolean;
}

export interface SetupStatus {
  steps: SetupStep[];
  dismissed: boolean;
  /** Every step done, optional ones included. */
  complete: boolean;
}

export const onboardingApi = {
  status: () => api.get<SetupStatus>("/onboarding/status"),
  setDismissed: (dismissed: boolean) => api.put<SetupStatus>("/onboarding/checklist", { dismissed }),
};
