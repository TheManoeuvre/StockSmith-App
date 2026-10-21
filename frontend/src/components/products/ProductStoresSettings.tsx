import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { productsApi } from "../../api/products";
import type { Product } from "../../api/types";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useManagedSave } from "../../hooks/useDirtyRegistry";
import { ErrorBanner } from "../common/ErrorBanner";

interface StoresSettingsForm {
  ceiling: string;
  pushBuildable: boolean;
}

const EMPTY_FORM: StoresSettingsForm = { ceiling: "", pushBuildable: true };

/**
 * The product-level marketplace settings that the reviewed design places on the Stores tab
 * rather than Details: the platform quantity ceiling and the "include buildable stock"
 * toggle (both edit a single column on the product, so they live here once, not per
 * platform). Edits are buffered and applied by the slide-over's footer Save, like every
 * other editor on the page.
 */
export function ProductStoresSettings({
  product,
  showBuildableToggle = true,
}: {
  product: Product;
  /** Bundles hold no build BOM, so "push buildable stock" is meaningless for them. */
  showBuildableToggle?: boolean;
}) {
  const queryClient = useQueryClient();

  const seed = useMemo<StoresSettingsForm>(
    () => ({
      ceiling:
        product.platform_ceiling_qty != null ? String(product.platform_ceiling_qty) : "",
      pushBuildable: product.push_buildable_capacity,
    }),
    [product.platform_ceiling_qty, product.push_buildable_capacity],
  );
  const {
    value: form,
    setValue: setForm,
    markSaved,
    revert,
  } = useEditableCopy<StoresSettingsForm>({
    key: "store-settings",
    label: "Store settings",
    initial: EMPTY_FORM,
    seed,
    seedKey: product.id,
  });

  const trimmed = form.ceiling.trim();
  const ceilingValue = trimmed === "" ? null : Number(trimmed);
  const ceilingInvalid =
    ceilingValue != null && (!Number.isFinite(ceilingValue) || ceilingValue < 0);

  const saveMutation = useMutation({
    mutationFn: () =>
      productsApi.update(product.id, {
        platform_ceiling_qty: ceilingValue,
        ...(showBuildableToggle ? { push_buildable_capacity: form.pushBuildable } : {}),
      }),
    onSuccess: () => {
      markSaved();
      queryClient.invalidateQueries({ queryKey: ["products", product.id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });
  useManagedSave("store-settings", {
    save: () => {
      if (!ceilingInvalid) saveMutation.mutate();
    },
    revert,
  });

  return (
    <div className="flex flex-col gap-2 rounded bg-white p-4 text-sm shadow-sm">
      <label className="flex items-center gap-3">
        <span className="shrink-0 whitespace-nowrap text-slate-600">
          Platform quantity ceiling
        </span>
        <span className="min-w-0 flex-1 text-right text-xs text-slate-400">
          caps advertised sellable per variant; blank = no cap
        </span>
        <input
          className="w-28 shrink-0 rounded border border-slate-300 px-2 py-1 text-right tabular-nums"
          placeholder="No cap"
          value={form.ceiling}
          onChange={(e) => setForm((prev) => ({ ...prev, ceiling: e.target.value }))}
        />
      </label>
      {ceilingInvalid && (
        <p className="text-xs text-red-600">Enter a whole number of 0 or more, or leave blank.</p>
      )}
      {showBuildableToggle && (
        <>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.pushBuildable}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, pushBuildable: e.target.checked }))
              }
            />
            Include buildable stock when pushing to marketplaces
          </label>
          <p className="text-xs text-slate-500">
            On by default: pushes advertise on-hand stock plus what could be built now from
            raw materials in stock. Turn off where build lead time makes that backfill risky.
          </p>
        </>
      )}

      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
