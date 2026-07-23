import { api } from "./client";

export type CurrencyCode = "GBP" | "USD" | "EUR";

export interface DefaultCurrency {
  default_currency: CurrencyCode;
}

export const appSettingsApi = {
  getDefaultCurrency: () => api.get<DefaultCurrency>("/settings/default-currency"),
  updateDefaultCurrency: (default_currency: CurrencyCode) =>
    api.put<DefaultCurrency>("/settings/default-currency", { default_currency }),
};
