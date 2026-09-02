import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { productCategoriesApi } from "../../api/productCategories";
import { stockCountSettingsApi } from "../../api/stockTakes";
import type { ABCClass, StockCountSettings as Settings } from "../../api/types";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { SettingsCard } from "./SettingsCard";

const TIERS: ABCClass[] = ["A", "B", "C"];
/** "Inherit" is a real choice here, not an empty one — it means "follow the baseline" and is
 * how a tier assignment gets cleared. Modelled as the empty string because a <select> has no
 * way to hold null. */
const INHERIT = "";

/**
 * Buffered rather than auto-saved, for the same reasons as ForecastSettings: the intervals are
 * numbers typed into free-text inputs, and the whole payload is written as one replacement, so
 * a per-keystroke save would repeatedly rewrite every tier assignment on the way through a
 * half-typed number.
 */
export function StockCountSettings() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["settings", "stock-count-settings"],
    queryFn: stockCountSettingsApi.get,
  });
  const { data: productCategories } = useQuery({ queryKey: ["product-categories"], queryFn: productCategoriesApi.list });
  // Categories are configurable now, so the list is whatever the shop has — including any the
  // user added, which is exactly where a per-category cadence earns its keep.
  const { categories } = useMaterialCategories();

  const {
    value: form,
    setValue: setForm,
    isDirty,
    isSeeded,
    markSaved,
  } = useEditableCopy<Settings | null>({
    key: "general/stock-count",
    label: "Stock counting",
    initial: null,
    seed: data,
    seedKey: "settings",
  });

  const updateMutation = useMutation({
    mutationFn: (settings: Settings) => stockCountSettingsApi.update(settings),
    onSuccess: (settings) => {
      markSaved(settings);
      queryClient.invalidateQueries({ queryKey: ["settings", "stock-count-settings"] });
      // Everything that displays a resolved tier or a due date is now stale.
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["stock-takes-overdue"] });
    },
  });
  const saveStatus = useSaveStatus(updateMutation.status);

  if (!isSeeded || !form) return null;

  const setIntervals = (scope: "material" | "product", tier: ABCClass, days: number) =>
    setForm((prev) => {
      if (!prev) return prev;
      const field = scope === "material" ? "material_tier_intervals" : "product_tier_intervals";
      return {
        ...prev,
        // Editing a cadence is what makes it an override; leaving it alone keeps it
        // following the shipped defaults if those ever change.
        [field]: prev[field].map((t) => (t.tier === tier ? { ...t, interval_days: days, is_override: true } : t)),
      };
    });

  const resetInterval = (scope: "material" | "product", tier: ABCClass) =>
    setForm((prev) => {
      if (!prev) return prev;
      const field = scope === "material" ? "material_tier_intervals" : "product_tier_intervals";
      return {
        ...prev,
        [field]: prev[field].map((t) => (t.tier === tier ? { ...t, is_override: false } : t)),
      };
    });

  const setCategoryTier = (categoryId: number, value: string) =>
    setForm((prev) => {
      if (!prev) return prev;
      const without = prev.category_tiers.filter((c) => c.category_id !== categoryId);
      return {
        ...prev,
        category_tiers:
          value === INHERIT
            ? without
            : [...without, { category_id: categoryId, abc_class: value as ABCClass }],
      };
    });

  const setProductCategoryTier = (productCategoryId: number, value: string) =>
    setForm((prev) => {
      if (!prev) return prev;
      const without = prev.product_category_tiers.filter((t) => t.product_category_id !== productCategoryId);
      return {
        ...prev,
        product_category_tiers:
          value === INHERIT ? without : [...without, { product_category_id: productCategoryId, abc_class: value as ABCClass }],
      };
    });

  const intervalsFor = (scope: "material" | "product") =>
    scope === "material" ? form.material_tier_intervals : form.product_tier_intervals;

  const renderIntervals = (scope: "material" | "product") => (
    <div className="flex flex-col gap-2">
      {TIERS.map((tier) => {
        const entry = intervalsFor(scope).find((t) => t.tier === tier);
        if (!entry) return null;
        return (
          <label key={tier} className="flex items-center gap-3 text-sm">
            <span className="w-36 shrink-0 text-slate-600">
              Tier {tier}
              {!entry.is_override && <span className="ml-1 text-xs text-slate-500">(default)</span>}
            </span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                min="1"
                step="1"
                className="w-24 rounded border border-slate-300 px-2 py-1 text-right tabular-nums"
                value={entry.interval_days}
                onChange={(e) => setIntervals(scope, tier, Number(e.target.value))}
              />
              <span className="text-xs text-slate-500">days</span>
              {entry.is_override && (
                <button
                  type="button"
                  className="text-xs text-slate-500 underline"
                  onClick={() => resetInterval(scope, tier)}
                  title="Stop overriding this and follow the shipped default"
                >
                  reset
                </button>
              )}
            </div>
          </label>
        );
      })}
    </div>
  );

  const tierSelect = (value: string, onChange: (next: string) => void) => (
    <select
      className="rounded border border-slate-300 px-2 py-1 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value={INHERIT}>Use default</option>
      {TIERS.map((t) => (
        <option key={t} value={t}>
          {t}
        </option>
      ))}
    </select>
  );

  return (
    <SettingsCard
      title="Stock counting"
      help="How often each thing should be physically counted. An item is counted at the cadence of its tier — A most often, C least — and anything past its cadence shows up as due on the dashboard. A tier set on an individual material or product wins over its category or type, which wins over the defaults below."
    >
      <div className="flex flex-col gap-3">
        <h3 className="text-sm font-medium">Materials</h3>
        <label className="flex items-center gap-3 text-sm">
          <span className="w-36 shrink-0 text-slate-600">Default tier</span>
          {tierSelect(form.default_material_abc_class, (next) =>
            setForm((prev) => (prev ? { ...prev, default_material_abc_class: next as ABCClass } : prev)),
          )}
        </label>
        {renderIntervals("material")}
        <div>
          <p className="mb-1 text-sm">By category</p>
          <div className="flex flex-wrap gap-3">
            {categories.map((category) => (
              <label key={category.id} className="flex items-center gap-1 text-sm">
                <span className="capitalize">{category.name}</span>
                {tierSelect(
                  form.category_tiers.find((c) => c.category_id === category.id)?.abc_class ?? INHERIT,
                  (next) => setCategoryTier(category.id, next),
                )}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-slate-200 pt-3">
        <h3 className="text-sm font-medium">Products</h3>
        <label className="flex items-center gap-3 text-sm">
          <span className="w-36 shrink-0 text-slate-600">Default tier</span>
          {tierSelect(form.default_product_abc_class, (next) =>
            setForm((prev) => (prev ? { ...prev, default_product_abc_class: next as ABCClass } : prev)),
          )}
        </label>
        {renderIntervals("product")}
        <div>
          <p className="mb-1 text-sm">By product category</p>
          {productCategories && productCategories.length > 0 ? (
            <div className="flex flex-wrap gap-3">
              {productCategories.map((type) => (
                <label key={type.id} className="flex items-center gap-1 text-sm">
                  {type.name}
                  {tierSelect(
                    form.product_category_tiers.find((t) => t.product_category_id === type.id)?.abc_class ?? INHERIT,
                    (next) => setProductCategoryTier(type.id, next),
                  )}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              No product categories yet — add some under Reference data to group products for counting.
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <SaveButton
          isDirty={isDirty}
          isPending={updateMutation.isPending}
          status={saveStatus}
          onClick={() => updateMutation.mutate(form)}
        >
          Save
        </SaveButton>
      </div>
      <ErrorBanner error={updateMutation.error} />
    </SettingsCard>
  );
}
