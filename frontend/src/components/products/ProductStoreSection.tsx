import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { platformsApi } from "../../api/platforms";
import type { ListingPlatform, Product } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { Switch } from "../common/Switch";

/** This product's on/off state for one store; unlisted means on, as it was before the switch existed. */
export function storeState(product: Pick<Product, "store_states">, platform: ListingPlatform) {
  return (
    product.store_states?.find((s) => s.platform === platform) ?? {
      platform,
      is_on: true,
      is_locked: false,
    }
  );
}

/**
 * One store's block on the product's Stores tab: a header carrying the on/off switch, and the
 * store's panels only while the product is on for it. Off collapses to a single line so a
 * product that never goes to eBay stops carrying eBay's setup, sync and log sections.
 *
 * A live listing pins the switch on — turning the store off would leave a listing selling
 * out from under a store StockSmith had stopped looking at.
 */
export function ProductStoreSection({
  product,
  platform,
  children,
}: {
  product: Product;
  platform: ListingPlatform;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const label = PLATFORM_LABELS[platform];
  const state = storeState(product, platform);

  const mutation = useMutation({
    mutationFn: (on: boolean) => platformsApi.setProductStore(platform, product.id, on),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["platforms", platform] });
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-md font-semibold">{label}</h3>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          {state.is_locked
            ? "Live: end the listing to turn off"
            : state.is_on
              ? `Sold on ${label}`
              : `Not sold on ${label}`}
          <Switch
            checked={state.is_on}
            disabled={state.is_locked || mutation.isPending}
            onChange={(on) => mutation.mutate(on)}
            ariaLabel={`Sold on ${label}`}
          />
        </label>
      </div>
      <ErrorBanner error={mutation.error} />
      {state.is_on ? (
        children
      ) : (
        <p className="rounded bg-white p-4 text-sm text-slate-500 shadow-sm">
          This product isn't sold on {label}. Its {label} setup, sync checks and warnings are hidden and
          skipped. Turn it on above to bring them back.
        </p>
      )}
    </div>
  );
}
