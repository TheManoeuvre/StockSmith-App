import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { feeConfigApi, type MarginFeeSource } from "../../api/feeConfig";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { BASIS_LABELS } from "./PlatformFeeComponents";

const SOURCE_LABELS: Record<MarginFeeSource, string> = {
  manual: "Manual — the flat % entered on each product",
  etsy: "Etsy — calculated from Etsy's fee components",
  ebay: "eBay — calculated from eBay's fee components",
};

/**
 * The one genuinely global pricing setting: which channel every margin figure is estimated for.
 *
 * It reads like a per-platform setting and isn't. A product shows a single margin number, so
 * there can only be one answer shop-wide — this is a lens, not a configuration of Etsy or eBay.
 * It also quietly selects which shipping cost column is used (services/shipping_profiles.py's
 * resolve_shipping_cost_for_fee_source maps it 1:1 onto cost_etsy/cost_ebay/cost_manual), which
 * is the other reason it can't be split per platform.
 *
 * The fee components it draws on are per-platform and live on the integration cards. The summary
 * below exists so that split doesn't read as arbitrary: you can see, from here, exactly which
 * numbers the current choice pulls in and where to go to change them.
 */
export function MarginFeeSettings() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: ["settings", "margin-fee-config"],
    queryFn: feeConfigApi.getMarginFeeConfig,
  });

  const sourceMutation = useMutation({
    mutationFn: (fee_source: MarginFeeSource) => feeConfigApi.updateMarginFeeConfig(fee_source),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "margin-fee-config"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const source = config?.fee_source ?? "manual";

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Margin estimate basis</p>
        <p className="text-sm text-slate-500">
          Which channel every product's "Platform fee" is estimated for. Applies shop-wide, and also selects
          which shipping cost each shipping profile contributes.
        </p>
      </div>
      <select
        aria-label="Margin estimate basis"
        className="w-fit rounded border border-slate-300 px-2 py-1 text-sm"
        value={source}
        onChange={(e) => sourceMutation.mutate(e.target.value as MarginFeeSource)}
      >
        {(Object.keys(SOURCE_LABELS) as MarginFeeSource[]).map((option) => (
          <option key={option} value={option}>
            {SOURCE_LABELS[option]}
          </option>
        ))}
      </select>
      <ErrorBanner error={sourceMutation.error} />

      {source === "manual" ? (
        <p className="text-sm text-slate-500">
          Each product's own "Platform fee %" is used as-is. Nothing is calculated from marketplace fee
          components.
        </p>
      ) : (
        <EffectiveFeeSummary platform={source} />
      )}
    </div>
  );
}

/** Read-only view of the components the selected basis pulls in, and where to edit them. */
function EffectiveFeeSummary({ platform }: { platform: ListingPlatform }) {
  const { data: components } = useQuery({
    queryKey: ["settings", "platform-fee-components", platform],
    queryFn: () => feeConfigApi.listFeeComponents(platform),
  });

  const enabled = components?.filter((c) => c.enabled) ?? [];

  return (
    <div className="flex flex-col gap-2 rounded bg-slate-50 p-3">
      <p className="text-sm font-medium">Currently applied — {PLATFORM_LABELS[platform]}</p>
      {enabled.length === 0 ? (
        <p className="text-sm text-slate-500">
          No fee components are enabled for {PLATFORM_LABELS[platform]}, so the calculated fee is zero.
        </p>
      ) : (
        <ul className="flex flex-col gap-0.5 text-sm text-slate-600">
          {enabled.map((c) => (
            <li key={c.id} className="flex justify-between gap-4">
              <span>
                {c.name} <span className="text-slate-400">({BASIS_LABELS[c.basis]})</span>
              </span>
              <span className="whitespace-nowrap tabular-nums">
                {c.rate_percent != null && `${c.rate_percent}%`}
                {c.rate_percent != null && c.fixed_amount != null && " + "}
                {c.fixed_amount != null && `£${c.fixed_amount}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      <Link
        to="/settings"
        search={{ tab: "integrations" }}
        className="self-start text-sm text-slate-600 underline"
      >
        Edit these under Integrations → {PLATFORM_LABELS[platform]}
      </Link>
    </div>
  );
}
