import { useQueries } from "@tanstack/react-query";
import { platformsApi } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";

export type StoreChoices = Partial<Record<ListingPlatform, boolean>>;

const LISTING_STORES: ListingPlatform[] = ["etsy", "ebay"];

/**
 * Which connected stores a product being created is for. Each chip starts at that store's
 * "on for new products" default; only the ones the user flips are reported back, so the caller
 * writes nothing for a store left alone.
 */
export function useNewProductStores() {
  const queries = useQueries({
    queries: LISTING_STORES.map((platform) => ({
      queryKey: ["platforms", platform, "status"],
      queryFn: () => platformsApi.status(platform),
      retry: false,
    })),
  });
  return LISTING_STORES.flatMap((platform, i) => {
    const status = queries[i]?.data;
    return status?.connected ? [{ platform, defaultOn: status.new_products_default_on }] : [];
  });
}

export function NewProductStoreChips({
  stores,
  choices,
  onChange,
}: {
  stores: { platform: ListingPlatform; defaultOn: boolean }[];
  choices: StoreChoices;
  onChange: (choices: StoreChoices) => void;
}) {
  if (stores.length === 0) return null;
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-sm">Sold on</legend>
      <div className="flex gap-2">
        {stores.map(({ platform, defaultOn }) => {
          const on = choices[platform] ?? defaultOn;
          return (
            <button
              key={platform}
              type="button"
              role="switch"
              aria-checked={on}
              onClick={() => onChange({ ...choices, [platform]: !on })}
              className={`rounded-full border px-3 py-1 text-sm ${
                on ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-500"
              }`}
            >
              {PLATFORM_LABELS[platform]}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
