import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { buildsApi, productsApi, stockAdjustmentsApi } from "../../api/products";
import { materialsApi } from "../../api/materials";
import { variantsApi } from "../../api/variants";
import type { ProductStockEvent } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { formatDayMonth, sellableSummary } from "../../lib/format";
import { useEditableCopy } from "../../hooks/useEditableCopy";

interface BuildForm {
  variantId: number | "";
  qtyBuilt: string;
  qtyFailed: string;
  notes: string;
  consumption: Record<number, boolean>;
}

// qtyBuilt starts at "1", not "": recording a single build should be one click.
const EMPTY_BUILD_FORM: BuildForm = { variantId: "", qtyBuilt: "1", qtyFailed: "0", notes: "", consumption: {} };

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
  const { variantId, qtyBuilt, qtyFailed, notes, consumption } = buildForm;
  const setBuildField = <K extends keyof BuildForm>(field: K, next: BuildForm[K]) =>
    setBuildForm((prev) => ({ ...prev, [field]: next }));
  const setVariantId = (next: number | "") => setBuildField("variantId", next);
  const setQtyBuilt = (next: string) => setBuildField("qtyBuilt", next);
  const setQtyFailed = (next: string) => setBuildField("qtyFailed", next);
  const setNotes = (next: string) => setBuildField("notes", next);
  const setConsumption = (updater: (prev: Record<number, boolean>) => Record<number, boolean>) =>
    setBuildForm((prev) => ({ ...prev, consumption: updater(prev.consumption) }));

  // listVariants doesn't carry effective_bom — only the single-get does — so without this the
  // "which materials were scrapped?" checkboxes never had rows to show for a variant build.
  const { data: fullSelectedVariant } = useQuery({
    queryKey: ["variants", variantId],
    queryFn: () => variantsApi.get(Number(variantId)),
    enabled: hasActiveVariants && variantId !== "",
  });
  const resolvedBom = hasActiveVariants
    ? fullSelectedVariant?.effective_bom ?? bom ?? []
    : bom ?? [];
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

  const buildMutation = useMutation({
    mutationFn: () =>
      buildsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(variantId) : null,
        qty_built: Number(qtyBuilt) || 0,
        qty_failed: qtyFailedNum,
        failed_consumption:
          qtyFailedNum > 0
            ? Object.fromEntries(resolvedBom.map((line) => [line.material_id, consumptionFor(line.material_id)]))
            : null,
        notes: notes || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "stock-history"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      // Resets the fields AND the dirty baseline together — a recorded build leaves nothing
      // unsaved, so navigating away must not prompt.
      markBuildDone(EMPTY_BUILD_FORM);
    },
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

  // The read-only figure block at the top of the tab — all client-derived, same inputs the
  // product header uses. Free stock is on-hand less what open orders have already claimed;
  // "once purchases land" only shows when inbound POs actually raise the ceiling.
  const onHand = hasActiveVariants
    ? activeVariants.reduce((sum, v) => sum + v.current_stock, 0)
    : product?.current_stock ?? 0;
  const allocated = hasActiveVariants
    ? activeVariants.reduce((sum, v) => sum + v.allocated_qty, 0)
    : product?.allocated_qty ?? 0;
  const sellable = product
    ? sellableSummary(product, {
        pushBuildableCapacity: product.push_buildable_capacity,
        platformCeilingQty: product.platform_ceiling_qty,
      })
    : null;

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
        return e.adjustment_mode === "set" ? `Set to ${e.adjustment_target_qty}` : e.reason;
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
      {sellable && (
        <div className="flex flex-col gap-2 rounded bg-white p-4 text-sm shadow-sm">
          <FigureRow
            label="Free stock"
            sub="on hand less what open orders have claimed"
            value={onHand - allocated}
          />
          <FigureRow label="Reserved to orders" value={allocated} />
          <FigureRow
            label="Buildable from materials"
            value={sellable.buildable == null ? "—" : sellable.buildable}
          />
          {sellable.expected != null && sellable.expected !== sellable.headline && (
            <FigureRow label="Once purchases land" value={sellable.expected} />
          )}
        </div>
      )}

      <div className="flex flex-col gap-3">
        <h3 className="text-md font-semibold">Record a build</h3>
        <form
          className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            buildMutation.mutate();
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
        <ErrorBanner error={buildMutation.error} />
      </div>

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
              <option value="set">Set exact amount</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">{adjMode === "set" ? "Set to" : "Adjust by"}</span>
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
            Setting an exact amount records a physical count, so this stops showing as due and its
            count date moves to today. Adjusting by an amount doesn't — a known change isn't a count.
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

/** One line in the top figures block: wide left title (with an optional sub-line),
 *  right-aligned value. */
function FigureRow({
  label,
  sub,
  value,
}: {
  label: string;
  sub?: string;
  value: string | number;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-slate-600">
        {label}
        {sub && <span className="ml-2 text-xs text-slate-400">{sub}</span>}
      </span>
      <span className="shrink-0 text-right tabular-nums font-medium">{value}</span>
    </div>
  );
}
