import { api } from "./client";
import type { ShippingProfile } from "./types";

export interface ShippingProfileInput {
  name: string;
  price?: string | null;
  cost_etsy?: string | null;
  cost_ebay?: string | null;
  cost_manual?: string | null;
  is_archived?: boolean;
}

export const shippingProfilesApi = {
  /** Active profiles only unless asked otherwise — archived ones stay out of the pickers. */
  list: (includeArchived = false) =>
    api.get<ShippingProfile[]>(`/shipping-profiles${includeArchived ? "?include_archived=true" : ""}`),
  create: (input: ShippingProfileInput) => api.post<ShippingProfile>("/shipping-profiles", input),
  update: (id: number, input: Record<string, unknown>) =>
    api.patch<ShippingProfile>(`/shipping-profiles/${id}`, input),
  remove: (id: number) => api.delete<void>(`/shipping-profiles/${id}`),
  merge: (id: number, targetId: number) =>
    api.post<ShippingProfile>(`/shipping-profiles/${id}/merge`, { target_id: targetId }),
};
