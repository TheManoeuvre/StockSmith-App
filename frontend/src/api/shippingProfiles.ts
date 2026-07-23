import { api } from "./client";
import type { ShippingProfile } from "./types";

export interface ShippingProfileInput {
  name: string;
  price?: string | null;
  cost_etsy?: string | null;
  cost_ebay?: string | null;
  cost_manual?: string | null;
}

export const shippingProfilesApi = {
  list: () => api.get<ShippingProfile[]>("/shipping-profiles"),
  create: (input: ShippingProfileInput) => api.post<ShippingProfile>("/shipping-profiles", input),
  update: (id: number, input: Partial<ShippingProfileInput>) =>
    api.patch<ShippingProfile>(`/shipping-profiles/${id}`, input),
  delete: (id: number) => api.delete<void>(`/shipping-profiles/${id}`),
};
