import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import { platformsApi, type UnitSyncResult } from "../../api/platforms";
import { productsApi } from "../../api/products";
import { variantsApi } from "../../api/variants";
import type { BomLineRead, KittingBomLineRead, Variant } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { PlatformSyncBadge } from "./PlatformSyncBadge";
import { BomOverrideEditor } from "./BomOverrideEditor";
import { sellableReasonTag } from "../../lib/format";

const INITIAL_VARIANT_LIMIT = 5;

export function VariantEditor({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId),
  });
  const { data: baseBom } = useQuery({
    queryKey: ["products", productId, "bom"],
    queryFn: () => productsApi.getBom(productId),
  });
  const { data: baseKittingBom } = useQuery({
    queryKey: ["products", productId, "kitting-bom"],
    queryFn: () => productsApi.getKittingBom(productId),
  });
  // Shares its query key/cache with PlatformSyncSection on the product page, so this
  // doesn't trigger a second network round trip when both are mounted at once. Etsy
  // only, for now — this inline per-variant row badge doesn't loop over every
  // connected platform the way the Platform Sync tab's sections do.
  const { data: etsySync } = useQuery({
    queryKey: ["platforms", "etsy", "products", productId, "sync-status"],
    queryFn: () => platformsApi.getProductSyncStatus("etsy", productId),
    retry: false,
  });
  const syncUnitByVariant = new Map((etsySync?.units ?? []).map((u) => [u.variant_id, u]));

  const [newVariantName, setNewVariantName] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showDisabled, setShowDisabled] = useState(false);
  const [showAllVariants, setShowAllVariants] = useState(false);

  const createVariantMutation = useMutation({
    mutationFn: () => productsApi.createVariant(productId, { variant_name: newVariantName }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      setNewVariantName("");
    },
  });

  const filteredVariants = (variants ?? []).filter((v) => showDisabled || v.is_active);
  const disabledCount = (variants ?? []).filter((v) => !v.is_active).length;
  const visibleVariants = showAllVariants ? filteredVariants : filteredVariants.slice(0, INITIAL_VARIANT_LIMIT);
  const hiddenCount = filteredVariants.length - visibleVariants.length;

  return (
    <div className="flex flex-col gap-3">
      {disabledCount > 0 && (
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={showDisabled} onChange={(e) => setShowDisabled(e.target.checked)} />
          Show disabled ({disabledCount})
        </label>
      )}

      {visibleVariants.map((variant) => (
        <VariantRow
          key={variant.id}
          variant={variant}
          baseBom={baseBom ?? []}
          baseKittingBom={baseKittingBom ?? []}
          productId={productId}
          expanded={expandedId === variant.id}
          onToggle={() => setExpandedId((id) => (id === variant.id ? null : variant.id))}
          syncUnit={syncUnitByVariant.get(variant.id)}
        />
      ))}

      {filteredVariants.length > INITIAL_VARIANT_LIMIT && (
        <button
          onClick={() => setShowAllVariants((v) => !v)}
          className="self-start text-sm text-slate-600 underline"
        >
          {showAllVariants ? "Show less" : `Show all ${filteredVariants.length} (${hiddenCount} more)`}
        </button>
      )}

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          createVariantMutation.mutate();
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm">New variant name</span>
          <input
            required
            className="rounded border border-slate-300 px-2 py-1"
            value={newVariantName}
            onChange={(e) => setNewVariantName(e.target.value)}
          />
        </label>
        <button type="submit" className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
          + Add variant
        </button>
      </form>
      <ErrorBanner error={createVariantMutation.error} />
    </div>
  );
}

function attributeBadges(variant: Variant): string[] {
  return [variant.attribute1_value, variant.attribute2_value, variant.attribute3_value].filter(
    (v): v is string => !!v
  );
}

function VariantRow({
  variant,
  baseBom,
  baseKittingBom,
  productId,
  expanded,
  onToggle,
  syncUnit,
}: {
  variant: Variant;
  baseBom: BomLineRead[];
  baseKittingBom: KittingBomLineRead[];
  productId: number;
  expanded: boolean;
  onToggle: () => void;
  syncUnit: UnitSyncResult | undefined;
}) {
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: fullVariant } = useQuery({
    queryKey: ["variants", variant.id],
    queryFn: () => variantsApi.get(variant.id),
    enabled: expanded,
  });

  const [name, setName] = useState(variant.variant_name);
  const [skuSuffix, setSkuSuffix] = useState(variant.sku_suffix ?? "");

  const invalidateVariants = () => {
    queryClient.invalidateQueries({ queryKey: ["variants", variant.id] });
    queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
  };

  const renameMutation = useMutation({
    mutationFn: () => variantsApi.update(variant.id, { variant_name: name, sku_suffix: skuSuffix || null }),
    onSuccess: invalidateVariants,
  });

  const toggleActiveMutation = useMutation({
    mutationFn: () => variantsApi.update(variant.id, { is_active: !variant.is_active }),
    onSuccess: invalidateVariants,
  });

  const badges = attributeBadges(variant);
  const renameStatus = useSaveStatus(renameMutation.status);

  return (
    <div className={`rounded bg-white shadow-sm ${!variant.is_active ? "opacity-60" : ""}`}>
      <div className="flex w-full items-center justify-between p-3">
        <button onClick={onToggle} className="flex flex-1 items-center gap-2 text-left">
          <span className="font-medium">{variant.variant_name}</span>
          {variant.full_sku && (
            <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600">
              {variant.full_sku}
            </span>
          )}
          {badges.map((b) => (
            <span key={b} className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {b}
            </span>
          ))}
          {!variant.is_active && (
            <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-700">Disabled</span>
          )}
          {syncUnit && <PlatformSyncBadge platform="etsy" status={syncUnit.status} />}
        </button>
        <span className="text-sm text-slate-500">
          On hand: {variant.current_stock} · Allocated: {variant.allocated_qty} · Free:{" "}
          {variant.current_stock - variant.allocated_qty} · Max buildable: {variant.max_buildable ?? "No BOM set"} ·
          Expected max buildable: {variant.expected_max_buildable ?? "No BOM set"} · Max sellable:{" "}
          {variant.max_sellable ?? "—"}
          {sellableReasonTag(variant.max_sellable, variant.max_buildable, variant.max_sellable_reason) && (
            <> {sellableReasonTag(variant.max_sellable, variant.max_buildable, variant.max_sellable_reason)}</>
          )}{" "}
          · Expected max sellable: {variant.expected_max_sellable ?? "—"}
          {sellableReasonTag(
            variant.expected_max_sellable,
            variant.expected_max_buildable,
            variant.expected_max_sellable_reason
          ) && (
            <>
              {" "}
              {sellableReasonTag(
                variant.expected_max_sellable,
                variant.expected_max_buildable,
                variant.expected_max_sellable_reason
              )}
            </>
          )}{" "}
          · Cost/unit: {variant.cost_per_unit ? `£${Number(variant.cost_per_unit).toFixed(2)}` : "—"}
        </span>
      </div>
      {expanded && (
        <div className="border-t border-slate-100 p-3">
          <div className="mb-3 flex items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-sm">Variant name</span>
              <input
                className="rounded border border-slate-300 px-2 py-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-sm">SKU suffix</span>
              <input
                className="rounded border border-slate-300 px-2 py-1"
                value={skuSuffix}
                onChange={(e) => setSkuSuffix(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-sm">Full SKU</span>
              <span className="rounded border border-transparent px-2 py-1 font-mono text-sm text-slate-500">
                {variant.full_sku ?? "—"}
              </span>
            </label>
            <button
              onClick={() => renameMutation.mutate()}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
            >
              Save
            </button>
            <SaveIndicator status={renameStatus} />
            <button
              onClick={() => toggleActiveMutation.mutate()}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
            >
              {variant.is_active ? "Disable" : "Reactivate"}
            </button>
          </div>
          <ErrorBanner error={renameMutation.error ?? toggleActiveMutation.error} />

          {fullVariant && materials && (
            <>
              <BomOverrideEditor
                title="Bill of Materials overrides"
                seedKey={variant.id}
                baseBom={baseBom}
                effectiveBom={fullVariant.effective_bom}
                materials={materials}
                onSave={(payload) => variantsApi.replaceBomOverrides(variant.id, payload)}
                onSaved={invalidateVariants}
              />
              <BomOverrideEditor
                title="Kitting BOM overrides"
                seedKey={variant.id}
                baseBom={baseKittingBom}
                effectiveBom={fullVariant.effective_kitting_bom}
                materials={materials}
                onSave={(payload) => variantsApi.replaceKittingBomOverrides(variant.id, payload)}
                onSaved={invalidateVariants}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
