import { api } from "./client";
import type { ListingPlatform } from "./types";
import type { LimitField } from "./platformLimits";

// Both the shipped default and any override are returned. An editor showing only the
// effective number would make an override indistinguishable from a default — so nobody
// could tell whether a surprising value came from StockSmith or from something they
// changed a year ago, and "reset" would have nothing to reset to.
export interface PlatformFieldLimitRead {
  field: LimitField;
  label: string;
  kind: "int" | "text";
  default_value: string | null;
  override_value: string | null;
  effective_value: string | null;
  is_override: boolean;
  note: string | null;
}

export interface PlatformFieldLimitWrite {
  int_value?: number | null;
  text_value?: string | null;
  note?: string | null;
}

export const platformConfigApi = {
  listLimits: (platform: ListingPlatform) =>
    api.get<PlatformFieldLimitRead[]>(`/settings/platform-limits/${platform}`),
  setLimit: (platform: ListingPlatform, field: LimitField, payload: PlatformFieldLimitWrite) =>
    api.put<PlatformFieldLimitRead>(`/settings/platform-limits/${platform}/${field}`, payload),
  clearLimit: (platform: ListingPlatform, field: LimitField) =>
    api.delete<void>(`/settings/platform-limits/${platform}/${field}`),
};
