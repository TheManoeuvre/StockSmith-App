import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type PlatformStatus } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { PLATFORM_LABELS } from "../../../lib/platforms";
import { ErrorBanner } from "../../common/ErrorBanner";
import { Modal } from "../../common/Modal";
import { Switch } from "../../common/Switch";
import { SettingsCard } from "../SettingsCard";

/**
 * Which products this store is for: the default for new products, how many are on today, and
 * "set everything on / off" for the rare bulk change.
 *
 * Also hosts the one-off prompt. Until the user has answered it once for this store
 * (product_scope_decided_at), opening this page asks whether the existing catalogue should be
 * on or off here. The answer is stored on the connection, so a disconnect/reconnect of the same
 * shop never asks again. "Decide later" only dismisses it for this visit.
 */
export function ProductScopeCard({ platform, status }: { platform: ListingPlatform; status: PlatformStatus }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState(false);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["platforms", platform] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
  };
  const scopeMutation = useMutation({
    mutationFn: (on: boolean) => platformsApi.applyProductScope(platform, on),
    onSuccess: refresh,
  });
  const defaultMutation = useMutation({
    mutationFn: (on: boolean) => platformsApi.setNewProductDefault(platform, on),
    onSuccess: refresh,
  });

  const total = status.products_on_count + status.products_off_count;
  const showPrompt = status.product_scope_decided_at === null && total > 0 && !dismissed;

  return (
    <SettingsCard
      title="Products on this store"
      help={`Products switched off for ${label} lose its setup, sync checks and warnings, and are skipped by its background checks.`}
    >
      <div className="flex items-center justify-between gap-3 text-sm">
        <label htmlFor={`new-default-${platform}`}>Turn on for new products by default</label>
        <Switch
          id={`new-default-${platform}`}
          checked={status.new_products_default_on}
          disabled={defaultMutation.isPending}
          onChange={(on) => defaultMutation.mutate(on)}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-slate-600">
          {status.products_on_count} on · {status.products_off_count} off
        </span>
        <span className="flex gap-2">
          <button
            type="button"
            disabled={scopeMutation.isPending}
            onClick={() => scopeMutation.mutate(true)}
            className="h-7 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium disabled:opacity-50"
          >
            Turn all on
          </button>
          <button
            type="button"
            disabled={scopeMutation.isPending}
            onClick={() => scopeMutation.mutate(false)}
            className="h-7 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium disabled:opacity-50"
          >
            Turn all off
          </button>
        </span>
      </div>
      <p className="text-xs text-slate-500">
        Products with a live {label} listing always stay on.
      </p>
      <ErrorBanner error={scopeMutation.error ?? defaultMutation.error} />

      {showPrompt && (
        <Modal
          title={`Which products are sold on ${label}?`}
          onClose={() => setDismissed(true)}
          footer={
            <>
              <button
                type="button"
                disabled={scopeMutation.isPending}
                onClick={() => setDismissed(true)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm disabled:opacity-50"
              >
                Decide later
              </button>
              <button
                type="button"
                disabled={scopeMutation.isPending}
                onClick={() => scopeMutation.mutate(false)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm disabled:opacity-50"
              >
                Turn all off
              </button>
              <button
                type="button"
                disabled={scopeMutation.isPending}
                onClick={() => scopeMutation.mutate(true)}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                Keep all on
              </button>
            </>
          }
        >
          <div className="flex flex-col gap-2 text-sm text-slate-600">
            <p>
              You can now choose, product by product, whether something is sold on {label}. Switching a
              product off hides its {label} setup and stops its {label} warnings and checks.
            </p>
            <p>
              Should your {total} existing product{total === 1 ? "" : "s"} start on or off for {label}? Products
              with a live {label} listing stay on either way, and you can change any of them later. You'll only
              be asked this once.
            </p>
          </div>
        </Modal>
      )}
    </SettingsCard>
  );
}
