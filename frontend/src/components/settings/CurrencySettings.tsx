import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { appSettingsApi, type CurrencyCode } from "../../api/appSettings";
import { ErrorBanner } from "../common/ErrorBanner";

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

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Default currency</p>
        <p className="text-sm text-slate-500">
          Pre-fills the currency on a new manual order — you can still change it per order. No conversion is
          applied anywhere; this is a label only.
        </p>
      </div>
      <select
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
      <ErrorBanner error={updateMutation.error} />
    </div>
  );
}
