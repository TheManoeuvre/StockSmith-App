import { useQuery } from "@tanstack/react-query";
import { appSettingsApi } from "../api/appSettings";
import { setDisplayCurrency } from "../lib/money";

/**
 * The currency every figure is shown in (Settings > Default currency).
 *
 * Feeds lib/money.ts's module-level value, like useMaterialUnits does for unit formatting.
 * The root layout calls it once, so the whole app agrees; changing the setting invalidates
 * the same query key, and the root re-keys the page so everything redraws with the new symbol.
 * Defaults to GBP until the setting loads (and if it never does), so nothing flashes blank.
 */
export function useDisplayCurrency(): string {
  const { data } = useQuery({
    queryKey: ["settings", "default-currency"],
    queryFn: appSettingsApi.getDefaultCurrency,
  });
  const code = data?.default_currency ?? "GBP";
  setDisplayCurrency(code);
  return code;
}
