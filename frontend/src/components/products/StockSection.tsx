import { Link } from "@tanstack/react-router";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { buildsApi, productsApi, stockAdjustmentsApi } from "../../api/products";
import { materialsApi } from "../../api/materials";
import { materialSubstitutesApi } from "../../api/materialSubstitutes";
import { variantsApi } from "../../api/variants";
import type { ProductStockEvent } from "../../api/types";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";
import { formatDayMonth, qtyWithUnit } from "../../lib/format";
import { useEditableCopy } from "../../hooks/useEditableCopy";

interface BuildForm {
  variantId: number | "";
  qtyBuilt: string;
  qtyFailed: string;
  notes: string;
  consumption: Record<number, boolean>;
  /** material_id -> substitute_material_id, for BOM lines the build can't cover from the
   *  material's own shelf. Only entries for materials still short at submit time are sent. */
  substitutions: Record<number, number>;
}

// qtyBuilt starts at "1", not "": recording a single build should be one click.
const EMPTY_BUILD_FORM: BuildForm = {
  variantId: "",
  qtyBuilt: "1",
  qtyFailed: "0",
  notes: "",
  consumption: {},
  substitutions: {},
};

interface AdjustForm {
  adjVariantId: number | "";
  adjMode: "adjust" | "set";
  adjValue: string;
  /** A preset from ADJUST_REASONS, or "" for the unpicked state. */
  adjReason: string;
  /** Free text, only when adjReason is OTHER_REASON. */
  adjReasonOther: string;
}

const EMPTY_ADJUST_FORM: AdjustForm = {
  adjVariantId: "",
  adjMode: "adjust",
  adjValue: "",
  adjReason: "",
  adjReasonOther: "",
};

// Built-stock adjustment presets, mirroring the Materials Stock tab. "Other…" reveals a
// free-text detail field so anything unusual is still one dropdown away.
const ADJUST_REASONS = [
  "Failed print / scrapped",
  "Built to stock",
  "Sample / giveaway",
  "Correction",
  "Other…",
] as const;
const OTHER_REASON = "Other…";

const EVENT_LABELS: Record<ProductStockEvent["event_type"], string> = {
  build_success: "Build",
  build_failed: "Failed build",
  adjustment: "Adjustment",
  order_fulfillment: "Order shipped",
};

const EVENT_BADGE: Record<ProductStockEvent["event_type"], string> = {
  build_success: "bg-blue-100 text-blue-800",
  build_failed: "bg-rose-100 text-rose-800",
  adjustment: "bg-slate-100 text-slate-700",
  order_fulfillment: "bg-amber-100 text-amber-800",
};

export function StockSection({
  productId,
  initialVariantId,
}: {
  productId: number;
  /** Preselects the build form's variant, from ?variantId on the product route — how the
   *  dashboard's "Build now" carries the variant the order is short of. */
  initialVariantId?: number;
}) {
  const queryClient = useQueryClient();
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const { data: product } = useQuery({
    queryKey: ["products", productId],
    queryFn: () => productsApi.get(productId),
  });
  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId),
  });
  const { data: bom } = useQuery({
    queryKey: ["products", productId, "bom"],
    queryFn: () => productsApi.getBom(productId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: () => materialsApi.list() });
  const { data: history } = useQuery({
    queryKey: ["products", productId, "stock-history"],
    queryFn: () => productsApi.listStockHistory(productId),
  });

  // A product whose variants are all disabled is treated as if it had none — the build
  // form falls back to the bare product's own SKU/BOM/stock rather than forcing a
  // (disabled) variant to be picked. History still shows the variant column whenever any
  // variant row ever existed, active or not, so past events keep their label.
  const activeVariants = (variants ?? []).filter((v) => v.is_active);
  const hasActiveVariants = activeVariants.length > 0;
  const hasAnyVariants = (variants?.length ?? 0) > 0;

  // Normally the defaults, but an inbound ?variantId preselects the variant. Returning
  // undefined holds the seed back until the variant list has loaded, which is what stops the
  // form seeding empty and then never re-seeding — useEditableCopy seeds once per seedKey and
  // ignores a later seed under the same one. The membership test means a stale or hand-edited
  // id lands on the ordinary empty form rather than selecting something that isn't offered.
  const buildSeed = useMemo(() => {
    if (initialVariantId == null) return EMPTY_BUILD_FORM;
    if (!variants) return undefined;
    return variants.some((v) => v.id === initialVariantId && v.is_active)
      ? { ...EMPTY_BUILD_FORM, variantId: initialVariantId }
      : EMPTY_BUILD_FORM;
  }, [initialVariantId, variants]);

  // A command form, not an editor of stored state — so it diffs against its own defaults
  // rather than server data. That still makes "dirty" meaningful (a typed note or a changed
  // qty warns on navigate-away), but the Record button is gated on validity instead: the qty
  // defaults to 1 precisely so recording one build is a single click.
  //
  // seedKey stays constant despite the seed now varying: switching tabs unmounts this, so a
  // fresh arrival re-seeds anyway, and keying on the variant would instead let the tab
  // switcher (which drops the search params) wipe a half-filled form.
  const {
    value: buildForm,
    setValue: setBuildForm,
    markSaved: markBuildDone,
  } = useEditableCopy<BuildForm>({
    key: "build",
    label: "Record a build",
    initial: EMPTY_BUILD_FORM,
    seed: buildSeed,
    seedKey: "const",
  });
  const { variantId, qtyBuilt, qtyFailed, notes, consumption, substitutions } = buildForm;
  const setBuildField = <K extends keyof BuildForm>(field: K, next: BuildForm[K]) =>
    setBuildForm((prev) => ({ ...prev, [field]: next }));
  const setVariantId = (next: number | "") => setBuildField("variantId", next);
  const setQtyBuilt = (next: string) => setBuildField("qtyBuilt", next);
  const setQtyFailed = (next: string) => setBuildField("qtyFailed", next);
  const setNotes = (next: string) => setBuildField("notes", next);
  const setConsumption = (updater: (prev: Record<number, boolean>) => Record<number, boolean>) =>
    setBuildForm((prev) => ({ ...prev, consumption: updater(prev.consumption) }));
  const setSubstitution = (materialId: number, substituteId: number | null) =>
    setBuildForm((prev) => {
      const next = { ...prev.substitutions };
      if (substituteId == null) delete next[materialId];
      else next[materialId] = substituteId;
      return { ...prev, substitutions: next };
    });
  const [substitutionConfirmOpen, setSubstitutionConfirmOpen] = useState(false);

  // listVariants doesn't carry effective_bom — only the single-get does — so without this the
  // "which materials were scrapped?" checkboxes never had rows to show for a variant build.
  const { data: fullSelectedVariant } = useQuery({
    queryKey: ["variants", variantId],
    queryFn: () => variantsApi.get(Number(variantId)),
    enabled: hasActiveVariants && variantId !== "",
  });
  const resolvedBomLines = hasActiveVariants
    ? fullSelectedVariant?.effective_bom ?? bom ?? []
    : bom ?? [];
  // One entry per material: the scrap checkboxes are a per-material decision, and a
  // variant can carry two lines on one material (both colour attributes resolving to the
  // same filament) that would otherwise render as duplicate rows.
  const resolvedBom = resolvedBomLines.filter(
    (line, i) => resolvedBomLines.findIndex((other) => other.material_id === line.material_id) === i,
  );
  const materialById = useMemo(() => new Map((materials ?? []).map((m) => [m.id, m])), [materials]);
  const { categories, byName: categoriesByName } = useMaterialCategories();
  const qtyFailedNum = Number(qtyFailed) || 0;

  const consumptionFor = (materialId: number) => {
    const material = materialById.get(materialId);
    return (
      consumption[materialId] ??
      (material ? categoriesByName.get(material.category)?.consumed_on_failed_build ?? false : false)
    );
  };

  // The categories that actually carry the flag, for the sentence below the checkboxes. Naming
  // filament there stopped being true the moment the flag became editable.
  const consumedByDefault = categories.filter((c) => c.consumed_on_failed_build).map((c) => c.name);

  // What this build would draw from each material versus what's on the shelf — the same
  // arithmetic the server applies when it writes the adjustments (built units always, failed
  // units only where the scrap checkbox says the run reached that material). A line that
  // comes up short is where a curated fallback can step in; the fallback-pooled capacity
  // figures up top already assume one will, so this is what makes that true in practice.
  const qtyBuiltNum = Number(qtyBuilt) || 0;
  const qtyPerUnitByMaterial = new Map<number, number>();
  for (const line of resolvedBomLines) {
    qtyPerUnitByMaterial.set(
      line.material_id,
      (qtyPerUnitByMaterial.get(line.material_id) ?? 0) + Number(line.qty_required),
    );
  }
  // Until a variant is picked, resolvedBomLines is the base BOM — not what will be built.
  const bomIsSettled = !hasActiveVariants || (variantId !== "" && fullSelectedVariant != null);
  const shortLines = [...qtyPerUnitByMaterial.entries()].flatMap(([materialId, qtyPerUnit]) => {
    const material = materialById.get(materialId);
    if (!material || !bomIsSettled) return [];
    const units = qtyBuiltNum + (consumptionFor(materialId) ? qtyFailedNum : 0);
    const needed = qtyPerUnit * units;
    const onHand = Number(material.current_qty);
    return needed > onHand ? [{ material, needed, onHand }] : [];
  });

  // Curated fallbacks, fetched only for the lines actually short. Availability comes from
  // the materials list already loaded rather than a second lookup per fallback.
  const substituteQueries = useQueries({
    queries: shortLines.map(({ material }) => ({
      queryKey: ["materials", material.id, "substitutes"],
      queryFn: () => materialSubstitutesApi.list(material.id),
    })),
  });
  // null while the lookup is still in flight, so a line doesn't flash "no fallbacks" before
  // its options arrive.
  const fallbacksFor = (materialId: number) => {
    const index = shortLines.findIndex((s) => s.material.id === materialId);
    const rows = index === -1 ? undefined : substituteQueries[index]?.data;
    if (rows === undefined) return null;
    return rows
      .filter((s) => s.is_active)
      .sort((a, b) => a.rank - b.rank || a.id - b.id)
      .map((s) => ({ substitute: s, material: materialById.get(s.substitute_material_id) ?? null }));
  };

  // Only swaps for materials still short count: a substitution picked for a line that
  // later stopped being short (qty lowered, stock received) must not silently go through.
  const activeSubstitutions = shortLines.flatMap(({ material, needed }) => {
    const substituteId = substitutions[material.id];
    const substitute = substituteId == null ? null : materialById.get(substituteId);
    return substitute ? [{ material, substitute, needed }] : [];
  });

  const buildMutation = useMutation({
    mutationFn: () =>
      buildsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(variantId) : null,
        qty_built: qtyBuiltNum,
        qty_failed: qtyFailedNum,
        failed_consumption:
          qtyFailedNum > 0
            ? Object.fromEntries(resolvedBom.map((line) => [line.material_id, consumptionFor(line.material_id)]))
            : null,
        substitutions:
          activeSubstitutions.length > 0
            ? Object.fromEntries(activeSubstitutions.map((s) => [s.material.id, s.substitute.id]))
            : null,
        notes: notes || null,
      }),
    onSuccess: () => {
      setSubstitutionConfirmOpen(false);
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "stock-history"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      // Resets the fields AND the dirty baseline together — a recorded build leaves nothing
      // unsaved, so navigating away must not prompt.
      markBuildDone(EMPTY_BUILD_FORM);
    },
    // The ErrorBanner sits under the form, behind an open dialog — close it so the failure
    // (say, the fallback itself running short) is actually readable.
    onError: () => setSubstitutionConfirmOpen(false),
  });

  const {
    value: adjForm,
    setValue: setAdjForm,
    markSaved: markAdjDone,
  } = useEditableCopy<AdjustForm>({
    key: "adjust",
    label: "Stock adjustment",
    initial: EMPTY_ADJUST_FORM,
    seed: EMPTY_ADJUST_FORM,
    seedKey: "const",
  });
  const { adjVariantId, adjMode, adjValue, adjReason, adjReasonOther } = adjForm;
  const setAdjField = <K extends keyof AdjustForm>(field: K, next: AdjustForm[K]) =>
    setAdjForm((prev) => ({ ...prev, [field]: next }));
  const setAdjVariantId = (next: number | "") => setAdjField("adjVariantId", next);
  const setAdjMode = (next: "adjust" | "set") => setAdjField("adjMode", next);
  const setAdjValue = (next: string) => setAdjField("adjValue", next);
  const setAdjReason = (next: string) => setAdjField("adjReason", next);
  const setAdjReasonOther = (next: string) => setAdjField("adjReasonOther", next);
  const effectiveAdjReason =
    adjReason === OTHER_REASON ? adjReasonOther.trim() : adjReason;

  const adjustMutation = useMutation({
    mutationFn: () =>
      stockAdjustmentsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(adjVariantId) : null,
        mode: adjMode,
        value: Number(adjValue),
        reason: effectiveAdjReason,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "stock-history"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      markAdjDone(EMPTY_ADJUST_FORM);
    },
  });

  // Gates the action buttons. Not the same question as "is this dirty" — the build form is
  // valid the moment it loads (qty 1), but only dirty once something is typed.
  const canRecordBuild =
    (Number(qtyBuilt) > 0 || qtyFailedNum > 0) && (!hasActiveVariants || variantId !== "");
  const canAdjust =
    adjValue.trim() !== "" && effectiveAdjReason !== "" && (!hasActiveVariants || adjVariantId !== "");

  // Client-derived from the same inputs the product header uses; drives the adjust preview.
  const onHand = hasActiveVariants
    ? activeVariants.reduce((sum, v) => sum + v.current_stock, 0)
    : product?.current_stock ?? 0;

  const variantName = (id: number | null) => variants?.find((v) => v.id === id)?.variant_name ?? "—";

  const eventComment = (e: ProductStockEvent) => {
    switch (e.event_type) {
      case "build_success":
        return e.build_qty_failed
          ? `${e.build_qty_built} built (${e.build_qty_failed} also failed this run)`
          : `${e.build_qty_built} built`;
      case "build_failed":
        return `${e.build_qty_failed} failed`;
      case "adjustment":
        // "Counted", not "Set to": this mode is a physical count, and the history is where
        // that shows — it is what dates the item and what the count sheet's no-movement mark
        // is measured from.
        return e.adjustment_mode === "set" ? `Counted ${e.adjustment_target_qty}` : e.reason;
      case "order_fulfillment":
        return e.order_id != null ? (
          <Link
            to="/orders/$orderId"
            params={{ orderId: String(e.order_id) }}
            className="underline"
          >
            {e.order_external_order_id
              ? `Order ${e.order_external_order_id}`
              : `Order #${e.order_id}`}
          </Link>
        ) : (
          (e.order_external_order_id ?? "—")
        );
      default:
        return "—";
    }
  };

  const visibleHistory =
    history && (historyExpanded ? history : history.slice(0, 10));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <h3 className="text-md font-semibold">Record a build</h3>
        <form
          className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            // A swap draws down a different material than the BOM names, so it never goes
            // through on the Record click alone — the dialog spells out exactly what will move.
            if (activeSubstitutions.length > 0) setSubstitutionConfirmOpen(true);
            else buildMutation.mutate();
          }}
        >
          {hasActiveVariants && (
            <label className="flex flex-col gap-1">
              <span className="text-sm">Variant</span>
              <select
                required
                className="rounded border border-slate-300 px-2 py-1"
                value={variantId}
                onChange={(e) => setVariantId(Number(e.target.value))}
              >
                <option value="" disabled>
                  Select variant…
                </option>
                {activeVariants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.variant_name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-sm">Qty built</span>
            <input
              type="number"
              min={0}
              className="w-24 rounded border border-slate-300 px-2 py-1"
              value={qtyBuilt}
              onChange={(e) => setQtyBuilt(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Qty failed</span>
            <input
              type="number"
              min={0}
              className="w-24 rounded border border-slate-300 px-2 py-1"
              value={qtyFailed}
              onChange={(e) => setQtyFailed(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-sm">Notes</span>
            <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <button
            type="submit"
            disabled={!canRecordBuild || buildMutation.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Record
          </button>
        </form>

        {qtyFailedNum > 0 && resolvedBom.length > 0 && (
          <div className="rounded bg-white p-4 text-sm shadow-sm">
            <p className="mb-2 text-slate-500">
              Which materials were consumed for the {qtyFailedNum} failed unit(s)?{" "}
              {consumedByDefault.length > 0 && (
                <span className="capitalize">{consumedByDefault.join(", ")}</span>
              )}
              {consumedByDefault.length > 0 && " is checked by default — "}
              {consumedByDefault.length === 0 && "Nothing is checked by default — "}
              uncheck anything the failed run never reached.
            </p>
            <div className="flex flex-wrap gap-4">
              {resolvedBom.map((line) => (
                <label key={line.material_id} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={consumptionFor(line.material_id)}
                    onChange={(e) => setConsumption((prev) => ({ ...prev, [line.material_id]: e.target.checked }))}
                  />
                  {materialById.get(line.material_id)?.name ?? `Material #${line.material_id}`}
                </label>
              ))}
            </div>
          </div>
        )}
        {shortLines.length > 0 && (
          <div className="flex flex-col gap-2 rounded border border-amber-200 bg-amber-50 p-4 text-sm">
            <p className="font-medium text-amber-900">Not enough material for this build</p>
            {shortLines.map(({ material, needed, onHand }) => {
              const fallbacks = fallbacksFor(material.id);
              const chosen = substitutions[material.id];
              return (
                <div key={material.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span>
                    <span className="font-medium">{material.name}</span>{" "}
                    <span className="text-slate-600">
                      — need {qtyWithUnit(needed, material.unit)}, have {qtyWithUnit(onHand, material.unit)}
                    </span>
                  </span>
                  {fallbacks === null ? null : fallbacks.length > 0 ? (
                    <label className="flex items-center gap-1.5">
                      <span className="text-slate-600">Use instead:</span>
                      <select
                        aria-label={`Substitute for ${material.name}`}
                        className="rounded border border-slate-300 bg-white px-2 py-1"
                        value={chosen ?? ""}
                        onChange={(e) =>
                          setSubstitution(material.id, e.target.value === "" ? null : Number(e.target.value))
                        }
                      >
                        <option value="">No substitute</option>
                        {fallbacks.map(({ substitute, material: fallback }) => (
                          <option key={substitute.id} value={substitute.substitute_material_id}>
                            {fallback?.name ?? substitute.substitute_material_name ?? `Material #${substitute.substitute_material_id}`}
                            {fallback ? ` (${qtyWithUnit(fallback.current_qty, fallback.unit)} on hand)` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <span className="text-slate-500">
                      No fallbacks set up —{" "}
                      <Link to="/materials/$materialId" params={{ materialId: String(material.id) }} className="underline">
                        add one on the material
                      </Link>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <ErrorBanner error={buildMutation.error} />
      </div>

      <ConfirmDialog
        open={substitutionConfirmOpen}
        title="Build with substitute materials?"
        tone="default"
        confirmLabel="Record build"
        busy={buildMutation.isPending}
        onCancel={() => setSubstitutionConfirmOpen(false)}
        onConfirm={() => buildMutation.mutate()}
        body={
          <>
            <p>This build will draw from the fallback material instead of what the BOM names:</p>
            <ul className="list-disc pl-5">
              {activeSubstitutions.map(({ material, substitute, needed }) => (
                <li key={material.id}>
                  <span className="font-medium">{qtyWithUnit(needed, substitute.unit)}</span> of{" "}
                  <span className="font-medium">{substitute.name}</span> in place of {material.name}
                </li>
              ))}
            </ul>
            <p className="text-slate-500">
              The BOM itself is unchanged; the swap is logged against this build.
            </p>
          </>
        }
      />

      <div className="flex flex-col gap-3">
        <h3 className="text-md font-semibold">Adjust built stock</h3>
        <form
          className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            adjustMutation.mutate();
          }}
        >
          {hasActiveVariants && (
            <label className="flex flex-col gap-1">
              <span className="text-sm">Variant</span>
              <select
                required
                className="rounded border border-slate-300 px-2 py-1"
                value={adjVariantId}
                onChange={(e) => setAdjVariantId(Number(e.target.value))}
              >
                <option value="" disabled>
                  Select variant…
                </option>
                {activeVariants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.variant_name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-sm">Mode</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={adjMode}
              onChange={(e) => setAdjMode(e.target.value as "adjust" | "set")}
            >
              <option value="adjust">Adjust (+/-)</option>
              <option value="set">Stock count (set exact amount)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">{adjMode === "set" ? "Counted" : "Adjust by"}</span>
            <input
              required
              type="number"
              className="w-24 rounded border border-slate-300 px-2 py-1"
              placeholder={adjMode === "set" ? "e.g. 53" : "e.g. -5 or 10"}
              value={adjValue}
              onChange={(e) => setAdjValue(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Reason</span>
            <select
              required
              className="rounded border border-slate-300 px-2 py-1"
              value={adjReason}
              onChange={(e) => setAdjReason(e.target.value)}
            >
              <option value="">Pick a reason…</option>
              {ADJUST_REASONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          {adjReason === OTHER_REASON && (
            <label className="flex flex-1 flex-col gap-1">
              <span className="text-sm">Reason detail</span>
              <input
                required
                className="rounded border border-slate-300 px-2 py-1"
                placeholder="Breakage, recount, …"
                value={adjReasonOther}
                onChange={(e) => setAdjReasonOther(e.target.value)}
              />
            </label>
          )}
          <button
            type="submit"
            disabled={!canAdjust || adjustMutation.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save
          </button>
        </form>
        {adjValue.trim() !== "" && product && !hasActiveVariants && (
          <p className="text-xs text-slate-500">
            On hand {onHand} →{" "}
            {adjMode === "set" ? Number(adjValue) : onHand + Number(adjValue)}
          </p>
        )}
        {adjMode === "set" && (
          <p className="text-xs text-slate-500">
            This is a stock count: the figure you enter is what you counted, so the item stops
            showing as due, its count date moves to today, and nothing it has done since counts
            as movement. Adjusting by an amount doesn't — a known change isn't a count.
          </p>
        )}
        <ErrorBanner error={adjustMutation.error} />
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-md font-semibold">Stock history</h3>
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Date</th>
              <th className="p-2">Type</th>
              {hasAnyVariants && <th className="p-2">Variant</th>}
              <th className="p-2">Delta</th>
              <th className="p-2">Balance</th>
              <th className="p-2">Comment</th>
            </tr>
          </thead>
          <tbody>
            {visibleHistory?.map((e) => (
              <tr key={e.id} className="border-b border-slate-100 last:border-0">
                <td className="p-2 text-slate-500">{formatDayMonth(e.created_at)}</td>
                <td className="p-2">
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${EVENT_BADGE[e.event_type]}`}
                  >
                    {EVENT_LABELS[e.event_type]}
                  </span>
                </td>
                {hasAnyVariants && (
                  <td className="p-2">{variantName(e.variant_id)}</td>
                )}
                <td
                  className={`p-2 tabular-nums ${e.qty_delta > 0 ? "text-green-700" : e.qty_delta < 0 ? "text-red-600" : "text-slate-500"}`}
                >
                  {e.qty_delta > 0 ? "+" : ""}
                  {e.qty_delta}
                </td>
                <td className="p-2 tabular-nums">{e.running_balance}</td>
                <td className="p-2">{eventComment(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {history && (historyExpanded || history.length > 10) && (
          <button
            type="button"
            onClick={() => setHistoryExpanded((v) => !v)}
            className="self-start rounded border border-slate-300 px-3 py-1.5 text-sm"
          >
            {historyExpanded ? "Show fewer" : "Show full history"}
          </button>
        )}
      </div>
    </div>
  );
}
