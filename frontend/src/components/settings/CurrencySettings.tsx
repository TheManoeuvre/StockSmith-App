import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { appSettingsApi, type CurrencyCode } from "../../api/appSettings";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { SettingsCard } from "./SettingsCard";

const CURRENCY_LABELS: Record<CurrencyCode, string> = {
  GBP: "£ GBP",
  USD: "$ USD",
  EUR: "€ EUR",
};

export function CurrencySettings() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings", "default-currency"],
    queryFn: appSettingsApi.getDefaultCurrency,
  });

  const updateMutation = useMutation({
    mutationFn: (default_currency: CurrencyCode) => appSettingsApi.updateDefaultCurrency(default_currency),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings", "default-currency"] }),
  });
  const saveStatus = useSaveStatus(updateMutation.status);

  return (
    <SettingsCard
      title="Default currency"
      help="Pre-fills the currency on a new manual order — you can still change it per order. No conversion is applied anywhere; this is a label only."
    >
      {/* Left on auto-save: a three-option select shows its whole value set, and undoing a
          mis-click costs one more click. What it lacked was any sign it had saved at all. */}
      <div className="flex items-center gap-2">
        <select
          aria-label="Default currency"
          className="w-fit rounded border border-slate-300 px-2 py-1 text-sm"
          value={data?.default_currency ?? "GBP"}
          onChange={(e) => updateMutation.mutate(e.target.value as CurrencyCode)}
        >
          {(Object.keys(CURRENCY_LABELS) as CurrencyCode[]).map((code) => (
            <option key={code} value={code}>
              {CURRENCY_LABELS[code]}
            </option>
          ))}
        </select>
        <SaveIndicator status={saveStatus} />
      </div>
      <ErrorBanner error={updateMutation.error} />
    </SettingsCard>
  );
}
