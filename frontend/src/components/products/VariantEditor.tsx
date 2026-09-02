import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { platformsApi, type UnitSyncResult } from "../../api/platforms";
import { productsApi } from "../../api/products";
import { variantsApi } from "../../api/variants";
import type { BomLineRead, KittingBomLineRead, Variant } from "../../api/types";
import { CopyButton } from "../common/CopyButton";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { DirtyPath, useManagedSave } from "../../hooks/useDirtyRegistry";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { PlatformSyncBadge } from "./PlatformSyncBadge";
import { BomOverrideEditor } from "./BomOverrideEditor";
import { sellableSummary } from "../../lib/format";
import { formatUnitCost } from "../../lib/money";

const INITIAL_VARIANT_LIMIT = 5;

export function VariantEditor({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  // Shares its query key/cache with the product detail page's own product query, so
  // mounting this tab alongside it (both under the same $productId route) doesn't
  // trigger a second network round trip — same dedup pattern as etsySync below.
  const { data: product } = useQuery({
    queryKey: ["products", productId],
    queryFn: () => productsApi.get(productId),
  });
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

  const guard = useGuard();
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
        // Every editor inside this row registers under `variant-<id>/`, so collapsing or
        // disabling the row can ask about exactly its own subtree and nothing else.
        <DirtyPath key={variant.id} segment={`variant-${variant.id}`}>
        <VariantRow
          variant={variant}
          baseBom={baseBom ?? []}
          baseKittingBom={baseKittingBom ?? []}
          productId={productId}
          expanded={expandedId === variant.id}
          onToggle={() => {
            // Prefix is the row about to be UNMOUNTED (the currently expanded one), not the
            // one being clicked — collapsing is what destroys the edits. With nothing
            // expanded there is nothing to lose, so skip the check entirely rather than
            // passing a prefix that matches everything.
            const toggle = () => setExpandedId((id) => (id === variant.id ? null : variant.id));
            if (expandedId == null) toggle();
            else guard.attempt(toggle, { prefix: `variant-${expandedId}/` });
          }}
          syncUnit={syncUnitByVariant.get(variant.id)}
          pushBuildableCapacity={product?.push_buildable_capacity ?? true}
        />
        </DirtyPath>
      ))}

      {filteredVariants.length > INITIAL_VARIANT_LIMIT && (
        <button
          onClick={() =>
            // Collapsing unmounts every row past the limit, so check all of them at once.
            guard.attempt(() => setShowAllVariants((v) => !v), {
              prefixes: showAllVariants
                ? filteredVariants.slice(INITIAL_VARIANT_LIMIT).map((v) => `variant-${v.id}/`)
                : [],
            })
          }
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

// Attribute values are usually what the variant is NAMED after ("Orange" / "Orange",
// "4 Stud Standard / Blue" / "4 Stud Standard", "Blue"), so a badge for one is a badge
// for something already on the row. Only the values the name doesn't already state earn
// their space.
function attributeBadges(variant: Variant): string[] {
  const name = variant.variant_name.toLowerCase();
  return [variant.attribute1_value, variant.attribute2_value, variant.attribute3_value]
    .filter((v): v is string => !!v)
    .filter((v) => !name.includes(v.toLowerCase()));
}

function VariantRow({
  variant,
  baseBom,
  baseKittingBom,
  productId,
  expanded,
  onToggle,
  syncUnit,
  pushBuildableCapacity,
}: {
  variant: Variant;
  baseBom: BomLineRead[];
  baseKittingBom: KittingBomLineRead[];
  productId: number;
  expanded: boolean;
  onToggle: () => void;
  syncUnit: UnitSyncResult | undefined;
  pushBuildableCapacity: boolean;
}) {
  const queryClient = useQueryClient();
  const guard = useGuard();
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: fullVariant } = useQuery({
    queryKey: ["variants", variant.id],
    queryFn: () => variantsApi.get(variant.id),
    enabled: expanded,
  });

  // Previously these were plain useState initialisers with no effect at all — the opposite
  // failure from the other editors: nothing could clobber an edit, but a rename made anywhere
  // else never reached this row, which then showed a stale name indefinitely.
  const seed = useMemo(
    () => ({ name: variant.variant_name, skuSuffix: variant.sku_suffix ?? "" }),
    [variant.variant_name, variant.sku_suffix]
  );
  const {
    value: renameValue,
    setValue: setRenameValue,
    isDirty: renameDirty,
    markSaved: markRenameSaved,
    revert: revertRename,
  } = useEditableCopy<{ name: string; skuSuffix: string }>({
    key: "rename",
    label: `Variant "${variant.variant_name}"`,
    initial: seed,
    seed,
    seedKey: variant.id,
  });
  const { name, skuSuffix } = renameValue;
  const setName = (next: string) => setRenameValue((prev) => ({ ...prev, name: next }));
  const setSkuSuffix = (next: string) => setRenameValue((prev) => ({ ...prev, skuSuffix: next }));

  const invalidateVariants = () => {
    queryClient.invalidateQueries({ queryKey: ["variants", variant.id] });
    queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
  };

  const renameMutation = useMutation({
    mutationFn: () => variantsApi.update(variant.id, { variant_name: name, sku_suffix: skuSuffix || null }),
    onSuccess: () => {
      markRenameSaved();
      invalidateVariants();
    },
  });

  const toggleActiveMutation = useMutation({
    mutationFn: () => variantsApi.update(variant.id, { is_active: !variant.is_active }),
    onSuccess: invalidateVariants,
  });

  const badges = attributeBadges(variant);
  const renameStatus = useSaveStatus(renameMutation.status);
  const renameManaged = useManagedSave("rename", {
    save: () => renameMutation.mutate(),
    revert: revertRename,
  });
  const sellable = sellableSummary(variant, { pushBuildableCapacity });

  return (
    <div className={`rounded bg-white shadow-sm ${!variant.is_active ? "opacity-60" : ""}`}>
      <div className="flex w-full items-center justify-between gap-3 p-3">
        {/* The copy button is a SIBLING of the toggle, never a child: nested buttons are
            invalid, and burying it inside would put "Copy …" into the row's accessible
            name. variant_name stays first for the same reason — that name is how the
            row is found. */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <button onClick={onToggle} className="flex min-w-0 items-center gap-2 text-left">
            <span className="font-medium">{variant.variant_name}</span>
            {variant.full_sku && (
              <span
                className="truncate rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600"
                title={variant.full_sku}
              >
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
          {variant.full_sku && <CopyButton value={variant.full_sku} label={`Copy ${variant.full_sku}`} />}
        </div>
        <div className="shrink-0 text-right">
          <div className="flex items-baseline justify-end gap-2">
            <strong className={`text-lg font-medium ${sellable.headline === 0 ? "text-red-600" : ""}`}>
              {sellable.headline ?? "—"}
            </strong>
            {sellable.capLabel && (
              <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">{sellable.capLabel}</span>
            )}
          </div>
          {/* Everything the old line spelled out is still reachable: the expanded row's
              BOM tables carry each material's own "Max theoretical" bottleneck, which is
              the actual answer to "why only this many?". */}
          <div className="text-xs text-slate-500">
            {sellable.builtFree} built + {sellable.buildable ?? 0} buildable ·{" "}
            {variant.cost_per_unit ? formatUnitCost(variant.cost_per_unit) : "—"}
          </div>
        </div>
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
            {!renameManaged && (
              <SaveButton
                isDirty={renameDirty}
                isPending={renameMutation.isPending}
                status={renameStatus}
                onClick={() => renameMutation.mutate()}
                className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                Save
              </SaveButton>
            )}
            <button
              // Never counted dirty (it saves immediately), but disabling can hide the row and
              // so unmount the editors inside it.
              onClick={() =>
                guard.attempt(() => toggleActiveMutation.mutate(), { prefix: `variant-${variant.id}/` })
              }
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
                dirtyKey="bom-overrides"
                baseBom={baseBom}
                effectiveBom={fullVariant.effective_bom}
                materials={materials}
                onSave={(payload) => variantsApi.replaceBomOverrides(variant.id, payload)}
                onSaved={invalidateVariants}
              />
              <BomOverrideEditor
                title="Kitting BOM overrides"
                seedKey={variant.id}
                dirtyKey="kitting-bom-overrides"
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
