import { api } from "./client";
import type { KittingBomLine, KittingBomLineRead } from "./types";

export type CurrencyCode = "GBP" | "USD" | "EUR";

export interface DefaultCurrency {
  default_currency: CurrencyCode;
}

export type DefaultKittingBomLineRead = Omit<KittingBomLineRead, "product_id">;

export const appSettingsApi = {
  getDefaultCurrency: () => api.get<DefaultCurrency>("/settings/default-currency"),
  updateDefaultCurrency: (default_currency: CurrencyCode) =>
    api.put<DefaultCurrency>("/settings/default-currency", { default_currency }),
  getDefaultKittingBom: () => api.get<DefaultKittingBomLineRead[]>("/settings/default-kitting-bom"),
  replaceDefaultKittingBom: (lines: KittingBomLine[]) =>
    api.put<DefaultKittingBomLineRead[]>("/settings/default-kitting-bom", lines),
};
