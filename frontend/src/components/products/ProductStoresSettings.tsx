import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { productsApi } from "../../api/products";
import type { Product } from "../../api/types";
import type { SellableSummary } from "../../lib/format";
import { ErrorBanner } from "../common/ErrorBanner";
import { FieldRow } from "../common/FieldRow";

/**
 * The product-level marketplace settings that the reviewed design places on the Stores tab
 * rather than Details: the platform quantity ceiling and the "include buildable stock"
 * toggle (both edit a single column on the product, so they live here once, not per
 * platform), plus a read-only breakdown of what a push would actually advertise.
 */
export function ProductStoresSettings({
  product,
  sellable,
  onHand,
  allocated,
  showBuildableToggle = true,
}: {
  product: Product;
  sellable: SellableSummary;
  onHand: number;
  allocated: number;
  /** Bundles hold no build BOM, so "push buildable stock" is meaningless for them. */
  showBuildableToggle?: boolean;
}) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["products", product.id] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
  };

  const ceilingMutation = useMutation({
    mutationFn: (platform_ceiling_qty: number | null) =>
      productsApi.update(product.id, { platform_ceiling_qty }),
    onSuccess: invalidate,
  });
  const buildableMutation = useMutation({
    mutationFn: (push_buildable_capacity: boolean) =>
      productsApi.update(product.id, { push_buildable_capacity }),
    onSuccess: invalidate,
  });

  // Buffered so the field isn't fighting the query cache on every keystroke; re-syncs when
  // a save lands (or another tab changes it).
  const serverCeiling =
    product.platform_ceiling_qty != null ? String(product.platform_ceiling_qty) : "";
  const [ceiling, setCeiling] = useState(serverCeiling);
  useEffect(() => setCeiling(serverCeiling), [serverCeiling]);

  const commitCeiling = () => {
    const trimmed = ceiling.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (next === (product.platform_ceiling_qty ?? null)) return;
    if (next != null && !Number.isFinite(next)) return;
    ceilingMutation.mutate(next);
  };

  return (
    <div className="flex flex-col gap-2 rounded bg-white p-4 text-sm shadow-sm">
      <FieldRow label="Platform quantity ceiling">
        <div className="flex items-center gap-2">
          <input
            className="w-28 rounded border border-slate-300 px-2 py-1"
            placeholder="No cap"
            value={ceiling}
            onChange={(e) => setCeiling(e.target.value)}
            onBlur={commitCeiling}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
          />
          <span className="text-xs text-slate-400">
            caps advertised sellable per variant; blank = no cap
          </span>
        </div>
      </FieldRow>
      {showBuildableToggle && (
        <>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={product.push_buildable_capacity}
              onChange={(e) => buildableMutation.mutate(e.target.checked)}
            />
            Include buildable stock when pushing to marketplaces
          </label>
          <p className="text-xs text-slate-500">
            On by default: pushes advertise on-hand stock plus what could be built now from
            raw materials in stock. Turn off where build lead time makes that backfill risky.
          </p>
        </>
      )}

      <div className="mt-1 flex flex-col gap-2 border-t border-slate-100 pt-3">
        <FieldRow label="Reserved to open orders">
          <span className="tabular-nums">{allocated}</span>
        </FieldRow>
        <FieldRow label="Max from free stock">
          <span className="tabular-nums">{onHand - allocated}</span>
        </FieldRow>
        <FieldRow label="Quantity that would push">
          <span className="tabular-nums">
            {sellable.headline == null ? "—" : sellable.headline}
            {sellable.expected != null &&
              sellable.expected !== sellable.headline && (
                <span className="ml-2 text-xs text-slate-400">
                  {sellable.expected} once purchases land
                </span>
              )}
          </span>
        </FieldRow>
      </div>

      <ErrorBanner error={ceilingMutation.error ?? buildableMutation.error} />
    </div>
  );
}
