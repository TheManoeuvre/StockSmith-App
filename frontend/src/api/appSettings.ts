import { api } from "./client";
import type { KittingBomLine, KittingBomLineRead } from "./types";

export type CurrencyCode = "GBP" | "USD" | "EUR";

export interface DefaultCurrency {
  default_currency: CurrencyCode;
}

export type DefaultKittingBomLineRead = Omit<KittingBomLineRead, "product_id">;

export interface ForecastSettings {
  forecast_warning_weeks: string;
  forecast_critical_weeks: string;
  forecast_lookback_weeks: number;
  default_lead_time_days: number;
}

/** One "what do you make?" option, with a preview of what choosing it sets up. */
export interface BusinessPreset {
  key: string;
  label: string;
  description: string;
  categories: string[];
  units: string[];
  material_types: string[];
}

export interface BusinessTypeState {
  /** Null on a fresh install that hasn't chosen yet — the first-launch picker shows on exactly that. */
  business_type: string | null;
  can_choose: boolean;
  presets: BusinessPreset[];
}

export const appSettingsApi = {
  getBusinessType: () => api.get<BusinessTypeState>("/settings/business-type"),
  setBusinessType: (business_type: string) =>
    api.put<BusinessTypeState>("/settings/business-type", { business_type }),
  getDefaultCurrency: () => api.get<DefaultCurrency>("/settings/default-currency"),
  updateDefaultCurrency: (default_currency: CurrencyCode) =>
    api.put<DefaultCurrency>("/settings/default-currency", { default_currency }),
  getDefaultKittingBom: () => api.get<DefaultKittingBomLineRead[]>("/settings/default-kitting-bom"),
  replaceDefaultKittingBom: (lines: KittingBomLine[]) =>
    api.put<DefaultKittingBomLineRead[]>("/settings/default-kitting-bom", lines),
  getForecastSettings: () => api.get<ForecastSettings>("/settings/forecast-settings"),
  updateForecastSettings: (settings: ForecastSettings) =>
    api.put<ForecastSettings>("/settings/forecast-settings", settings),
};
