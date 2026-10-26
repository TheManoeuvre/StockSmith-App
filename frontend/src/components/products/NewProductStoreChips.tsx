import { useQueries } from "@tanstack/react-query";
import { platformsApi } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";

export type StoreChoices = Partial<Record<ListingPlatform, boolean>>;

const LISTING_STORES: ListingPlatform[] = ["etsy", "ebay"];

// Each store's own hue: solid when the product is for it, a pale outline of the same hue when
// not, so an off chip is still recognisably that store.
type ChipColour = { on: string; off: string; dot: string };
const SLATE_CHIP: ChipColour = {
  on: "border-slate-800 bg-slate-800 text-white",
  off: "border-slate-300 bg-white text-slate-600",
  dot: "bg-slate-300",
};
const CHIP_COLOURS: Partial<Record<ListingPlatform, ChipColour>> = {
  etsy: {
    on: "border-orange-600 bg-orange-600 text-white",
    off: "border-orange-200 bg-white text-orange-700",
    dot: "bg-orange-300",
  },
  ebay: {
    on: "border-blue-600 bg-blue-600 text-white",
    off: "border-blue-200 bg-white text-blue-700",
    dot: "bg-blue-300",
  },
};

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
    <fieldset className="flex items-center gap-2">
      <legend className="sr-only">Sold on</legend>
      <span aria-hidden className="mr-0.5 text-xs font-medium text-slate-600">
        Sold on
      </span>
      {stores.map(({ platform, defaultOn }) => {
        const on = choices[platform] ?? defaultOn;
        const colour = CHIP_COLOURS[platform] ?? SLATE_CHIP;
        return (
          <button
            key={platform}
            type="button"
            role="switch"
            aria-checked={on}
            onClick={() => onChange({ ...choices, [platform]: !on })}
            className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium ${
              on ? colour.on : colour.off
            }`}
          >
            <span aria-hidden className={`h-2 w-2 rounded-full ${on ? "bg-white" : colour.dot}`} />
            {PLATFORM_LABELS[platform]}
          </button>
        );
      })}
    </fieldset>
  );
}
