import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  feeConfigApi,
  type MarginFeeSource,
  type PlatformFeeComponent,
} from "../../api/feeConfig";
import { productsApi } from "../../api/products";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import { variantsApi } from "../../api/variants";
import type { PricingMode, Product, ShippingProfile, Variant } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { DirtyPath } from "../../hooks/useDirtyRegistry";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { formatUnitCost } from "../../lib/money";

const INITIAL_LINE_LIMIT = 5;

const MODE_LABELS: Record<PricingMode, string> = {
  product: "Product (one price for all variations)",
  variable: "Variable (by attribute, e.g. colour or size)",
  line: "Line (each variant priced independently)",
};

interface MarginInputs {
  sale_price: string | null;
  shipping_profile_id: number | null;
  effective_platform_fee_percent: string | null;
  cost_per_unit: string | null;
  kitting_cost_per_unit: string | null;
}

// Mirrors backend services/shipping_profiles.py::resolve_shipping_cost_for_fee_source —
// the shop-wide "Margin fee source" switch (Settings -> Pricing) stands in for "which
// channel am I estimating margin for," since there's no real order to key off yet.
function shippingCostForFeeSource(profile: ShippingProfile, feeSource: MarginFeeSource | undefined): number {
  if (feeSource === "etsy") return Number(profile.cost_etsy);
  if (feeSource === "ebay") return Number(profile.cost_ebay);
  return Number(profile.cost_manual);
}

function computeMargin(
  inputs: MarginInputs,
  profiles: ShippingProfile[],
  feeSource: MarginFeeSource | undefined
): { profit: number; marginPercent: number; postageMissing: boolean } | null {
  if (!inputs.sale_price) return null;
  const salePrice = Number(inputs.sale_price);
  const cost = inputs.cost_per_unit ? Number(inputs.cost_per_unit) : 0;
  // Packaging counts here too, so product margin and order net profit agree about whether
  // it's a cost. Per-unit is the pessimistic single-unit case — an order shipping several
  // units together pays for one box, not one per unit.
  const kitting = inputs.kitting_cost_per_unit ? Number(inputs.kitting_cost_per_unit) : 0;
  const profile = profiles.find((p) => p.id === inputs.shipping_profile_id);
  // No profile means postage is unknown, not free. The figure is still shown — it stays
  // directionally useful, and blanking it would hide the margin on every product that has
  // this gap — but it is flagged rather than passed off as complete. Same choice as the
  // order page's "No postage cost", and for the same reason: silently treating an unknown
  // as zero is exactly what let £95 of real postage sit outside reported profit.
  const postageMissing = profile === undefined;
  const shipping = profile ? shippingCostForFeeSource(profile, feeSource) : 0;
  const fee = (salePrice * (inputs.effective_platform_fee_percent ? Number(inputs.effective_platform_fee_percent) : 0)) / 100;
  const profit = salePrice - cost - kitting - shipping - fee;
  const marginPercent = salePrice !== 0 ? (profit / salePrice) * 100 : 0;
  return { profit, marginPercent, postageMissing };
}

/** The margin figure plus, when postage is unknown, a note saying the figure excludes it. */
function MarginSummary({ margin }: { margin: { profit: number; marginPercent: number; postageMissing: boolean } }) {
  return (
    <span className="text-sm">
      Profit: <strong>£{margin.profit.toFixed(2)}</strong> · Margin: <strong>{margin.marginPercent.toFixed(1)}%</strong>
      {margin.postageMissing && (
        <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800" title="No shipping profile is assigned, so postage is not deducted here — and an order for this product will ship with no postage cost recorded at all.">
          excludes postage
        </span>
      )}
    </span>
  );
}

function ShippingProfileSelect({
  profiles,
  value,
  onChange,
  inheritLabel,
  feeSource,
}: {
  profiles: ShippingProfile[];
  value: string;
  onChange: (value: string) => void;
  inheritLabel?: string;
  feeSource: MarginFeeSource | undefined;
}) {
  return (
    <select
      className="w-40 rounded border border-slate-300 px-2 py-1"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{inheritLabel ?? "No shipping profile"}</option>
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name} (cost £{shippingCostForFeeSource(p, feeSource).toFixed(2)})
        </option>
      ))}
    </select>
  );
}

function attributeValue(variant: Variant, index: 1 | 2 | 3): string | null {
  if (index === 1) return variant.attribute1_value;
  if (index === 2) return variant.attribute2_value;
  return variant.attribute3_value;
}

/** <30% red, 30–45% amber, otherwise the default text colour. Mirrors the design's
 *  margin-health thresholds. */
function marginToneClass(pct: number | null): string {
  if (pct == null) return "text-slate-500";
  if (pct < 30) return "text-red-600";
  if (pct < 45) return "text-amber-700";
  return "text-slate-700";
}

/** Read-only cost-of-goods rows under the active pricing form. Every input is already on
 *  the product (same figures the panel header's Margin line uses) — nothing new fetched. */
function CogsBreakdown({ product }: { product: Product }) {
  const materials = product.cost_per_unit != null ? Number(product.cost_per_unit) : null;
  const packaging =
    product.kitting_cost_per_unit != null ? Number(product.kitting_cost_per_unit) : 0;
  const postage =
    product.effective_shipping_cost != null
      ? Number(product.effective_shipping_cost)
      : null;
  const totalCogs = materials == null ? null : materials + packaging + (postage ?? 0);
  const salePrice = product.sale_price != null ? Number(product.sale_price) : null;
  const marginBeforeFees =
    totalCogs != null && salePrice != null && salePrice > 0
      ? ((salePrice - totalCogs) / salePrice) * 100
      : null;

  const money = (n: number | null) => (n == null ? "—" : `£${n.toFixed(2)}`);

  return (
    <div className="flex flex-col gap-1 rounded bg-white p-4 text-sm shadow-sm">
      <h4 className="mb-1 text-sm font-medium text-slate-600">Cost of goods</h4>
      <Row label="Materials" value={money(materials)} />
      <Row label="Packaging" value={money(packaging)} />
      <Row
        label="Postage"
        value={
          postage == null ? (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
              no profile
            </span>
          ) : (
            money(postage)
          )
        }
      />
      <div className="mt-1 border-t border-slate-100 pt-1">
        <Row label="Total COGS" value={<strong>{money(totalCogs)}</strong>} />
      </div>
      <Row
        label="Margin before fees"
        value={
          <strong className={marginToneClass(marginBeforeFees)}>
            {marginBeforeFees == null ? "—" : `${marginBeforeFees.toFixed(1)}%`}
          </strong>
        }
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-slate-500">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

/** Replicates services/platform_fees.compute_effective_fee_amount client-side. */
function effectiveFeeAmount(
  components: PlatformFeeComponent[],
  salePrice: number,
  shippingPrice: number,
): number {
  let subtotal = 0;
  for (const c of [...components]
    .filter((c) => c.enabled)
    .sort((a, b) => a.display_order - b.display_order)) {
    if (c.basis === "fees_subtotal") {
      if (c.rate_percent) subtotal += (subtotal * Number(c.rate_percent)) / 100;
      continue;
    }
    const basis = c.basis === "sale_price" ? salePrice : salePrice + shippingPrice;
    if (c.rate_percent) subtotal += (basis * Number(c.rate_percent)) / 100;
    if (c.fixed_amount) subtotal += Number(c.fixed_amount);
  }
  return subtotal;
}

/** Read-only Etsy vs eBay comparison: fee %, fee £ and resulting margin at the product's
 *  own sale price. No overrides — that stays a backlog item. */
function ChannelFeeComparison({
  product,
  profiles,
}: {
  product: Product;
  profiles: ShippingProfile[];
}) {
  const { data: etsyComponents } = useQuery({
    queryKey: ["settings", "platform-fee-components", "etsy"],
    queryFn: () => feeConfigApi.listFeeComponents("etsy"),
  });
  const { data: ebayComponents } = useQuery({
    queryKey: ["settings", "platform-fee-components", "ebay"],
    queryFn: () => feeConfigApi.listFeeComponents("ebay"),
  });

  const salePrice = product.sale_price != null ? Number(product.sale_price) : null;
  if (salePrice == null || salePrice <= 0) return null;

  const profile = profiles.find(
    (p) => p.id === product.effective_shipping_profile_id,
  );
  const shippingPrice = profile ? Number(profile.price) : 0;
  const materials = product.cost_per_unit != null ? Number(product.cost_per_unit) : null;
  const packaging =
    product.kitting_cost_per_unit != null ? Number(product.kitting_cost_per_unit) : 0;
  const postage =
    product.effective_shipping_cost != null
      ? Number(product.effective_shipping_cost)
      : 0;
  const totalCogs = materials == null ? null : materials + packaging + postage;

  const rows: { platform: string; components: PlatformFeeComponent[] | undefined }[] = [
    { platform: "Etsy", components: etsyComponents },
    { platform: "eBay", components: ebayComponents },
  ];

  return (
    <div className="flex flex-col gap-1 rounded bg-white p-4 text-sm shadow-sm">
      <h4 className="mb-1 text-sm font-medium text-slate-600">
        Channel fees at £{salePrice.toFixed(2)}
      </h4>
      <table className="w-full text-left">
        <thead>
          <tr className="text-xs text-slate-400">
            <th className="py-1 font-normal">Channel</th>
            <th className="py-1 text-right font-normal">Fee %</th>
            <th className="py-1 text-right font-normal">Fee £</th>
            <th className="py-1 text-right font-normal">Margin</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ platform, components }) => {
            if (!components) {
              return (
                <tr key={platform} className="border-t border-slate-100">
                  <td className="py-1">{platform}</td>
                  <td className="py-1 text-right text-slate-400" colSpan={3}>
                    …
                  </td>
                </tr>
              );
            }
            const feeAmount = effectiveFeeAmount(components, salePrice, shippingPrice);
            const feePct = (feeAmount / salePrice) * 100;
            const margin =
              totalCogs == null
                ? null
                : ((salePrice - totalCogs - feeAmount) / salePrice) * 100;
            return (
              <tr key={platform} className="border-t border-slate-100">
                <td className="py-1">{platform}</td>
                <td className="py-1 text-right tabular-nums">{feePct.toFixed(1)}%</td>
                <td className="py-1 text-right tabular-nums">£{feeAmount.toFixed(2)}</td>
                <td
                  className={`py-1 text-right tabular-nums ${marginToneClass(margin)}`}
                >
                  {margin == null ? "—" : `${margin.toFixed(1)}%`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProductDefaultShippingProfile({
  product,
  profiles,
  feeSource,
  onSaved,
}: {
  product: Product;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const saveMutation = useMutation({
    mutationFn: (shipping_profile_id: number | null) => productsApi.update(product.id, { shipping_profile_id }),
    onSuccess: onSaved,
  });

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="font-medium">Default shipping profile</span>
      <ShippingProfileSelect
        profiles={profiles}
        value={product.shipping_profile_id != null ? String(product.shipping_profile_id) : ""}
        onChange={(value) => saveMutation.mutate(value ? Number(value) : null)}
        feeSource={feeSource}
      />
      <ErrorBanner error={saveMutation.error} />
    </label>
  );
}

export function PricingSection({ product }: { product: Product }) {
  const queryClient = useQueryClient();
  const { data: variants } = useQuery({
    queryKey: ["products", product.id, "variants"],
    queryFn: () => productsApi.listVariants(product.id),
  });
  const { data: history } = useQuery({
    queryKey: ["products", product.id, "price-history"],
    queryFn: () => productsApi.getPriceHistory(product.id),
  });
  const { data: feeConfig } = useQuery({
    queryKey: ["settings", "margin-fee-config"],
    queryFn: feeConfigApi.getMarginFeeConfig,
  });
  const { data: shippingProfiles } = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    // Wrapped, not passed by reference: React Query calls queryFn with a context object,
    // which as a positional arg would read as includeArchived=true and put retired
    // profiles back into this picker.
    queryFn: () => shippingProfilesApi.list(),
  });
  const guard = useGuard();
  const isCalculatedFee = feeConfig?.fee_source != null && feeConfig.fee_source !== "manual";
  const profiles = shippingProfiles ?? [];

  const invalidateProduct = () => {
    queryClient.invalidateQueries({ queryKey: ["products", product.id] });
    queryClient.invalidateQueries({ queryKey: ["products", product.id, "variants"] });
    queryClient.invalidateQueries({ queryKey: ["products", product.id, "price-history"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const attributeNames: Record<1 | 2 | 3, string | null> = {
    1: product.variant_attribute1_name,
    2: product.variant_attribute2_name,
    3: product.variant_attribute3_name,
  };
  const attributeOptions = ([1, 2, 3] as const)
    .map((n) => ({ n, name: attributeNames[n] }))
    .filter((o): o is { n: 1 | 2 | 3; name: string } => !!o.name);

  const modeMutation = useMutation({
    mutationFn: (pricing_mode: PricingMode) => {
      const update: { pricing_mode: PricingMode; pricing_variable_attribute?: number } = { pricing_mode };
      if (pricing_mode === "variable" && !product.pricing_variable_attribute && attributeOptions.length > 0) {
        update.pricing_variable_attribute = attributeOptions[0].n;
      }
      return productsApi.update(product.id, update);
    },
    onSuccess: invalidateProduct,
  });

  const attributeMutation = useMutation({
    mutationFn: (pricing_variable_attribute: number) =>
      productsApi.update(product.id, { pricing_variable_attribute }),
    onSuccess: invalidateProduct,
  });

  const activeVariants = (variants ?? []).filter((v) => v.is_active);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded bg-white p-3 shadow-sm">
        <label className="flex items-center gap-2 text-sm">
          <span className="font-medium">Pricing mode</span>
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={product.pricing_mode}
            onChange={(e) => {
              const mode = e.target.value as PricingMode;
              guard.attempt(() => modeMutation.mutate(mode), { prefix: "pricing/" });
            }}
          >
            {(Object.keys(MODE_LABELS) as PricingMode[]).map((mode) => (
              <option key={mode} value={mode}>
                {MODE_LABELS[mode]}
              </option>
            ))}
          </select>
        </label>
        {product.pricing_mode === "variable" && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-slate-500">Vary by</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={product.pricing_variable_attribute ?? ""}
              onChange={(e) => {
                const attr = Number(e.target.value);
                guard.attempt(() => attributeMutation.mutate(attr), { prefix: "pricing/" });
              }}
            >
              {attributeOptions.length === 0 && <option value="">No attributes set up</option>}
              {attributeOptions.map((o) => (
                <option key={o.n} value={o.n}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {product.pricing_mode !== "product" && (
          <ProductDefaultShippingProfile
            product={product}
            profiles={profiles}
            feeSource={feeConfig?.fee_source}
            onSaved={invalidateProduct}
          />
        )}
        {activeVariants.length === 0 && (
          <span className="text-sm text-slate-500">Variable/line pricing needs at least one active variant.</span>
        )}
      </div>
      <ErrorBanner error={modeMutation.error ?? attributeMutation.error} />
      {isCalculatedFee && (
        <p className="text-sm text-slate-500">
          Platform fee is calculated from {feeConfig?.fee_source} fee components (Settings → Pricing) — the manual
          fee field below is ignored while this is active.
        </p>
      )}

      {/* Everything below registers under `pricing/`, so the mode and vary-by selects above
          — which unmount whichever form is showing — can ask about exactly this subtree. */}
      <DirtyPath segment="pricing">
        {product.pricing_mode === "product" && (
          <ProductPriceForm
            product={product}
            isCalculatedFee={isCalculatedFee}
            profiles={profiles}
            feeSource={feeConfig?.fee_source}
            onSaved={invalidateProduct}
          />
        )}
        {product.pricing_mode === "variable" && product.pricing_variable_attribute && (
          <VariablePriceGroups
            product={product}
            variants={activeVariants}
            attributeIndex={product.pricing_variable_attribute as 1 | 2 | 3}
            isCalculatedFee={isCalculatedFee}
            profiles={profiles}
            feeSource={feeConfig?.fee_source}
            onSaved={invalidateProduct}
          />
        )}
        {product.pricing_mode === "line" && (
          <LinePriceTable
            product={product}
            variants={activeVariants}
            isCalculatedFee={isCalculatedFee}
            profiles={profiles}
            feeSource={feeConfig?.fee_source}
            onSaved={invalidateProduct}
          />
        )}
      </DirtyPath>

      <CogsBreakdown product={product} />
      <ChannelFeeComparison product={product} profiles={profiles} />

      {history && history.length > 0 && (
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Date</th>
              <th className="p-2">Cost/unit</th>
              <th className="p-2">Sale price</th>
              <th className="p-2">Margin</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id} className="border-b border-slate-100">
                <td className="p-2">{new Date(h.recorded_at).toLocaleString()}</td>
                <td className="p-2">{formatUnitCost(h.cost_per_unit)}</td>
                <td className="p-2">{h.sale_price ? `£${Number(h.sale_price).toFixed(2)}` : "—"}</td>
                <td className="p-2">{h.margin_percent ? `${Number(h.margin_percent).toFixed(1)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface PriceFields {
  salePrice: string;
  shippingProfileId: string;
  platformFeePercent: string;
}

const EMPTY_PRICE_FIELDS: PriceFields = { salePrice: "", shippingProfileId: "", platformFeePercent: "" };

/**
 * The three pricing forms (product-wide, per attribute-value group, per line) edit the same
 * three fields against different entities and different save endpoints, so they share this.
 *
 * `project` is the point of interest: when the shop-wide fee source is a calculated one the
 * platform-fee input is hidden, but the value is still held and still sent. Without excluding
 * it from the comparison a form could sit there reporting unsaved changes to a field nobody
 * can see or edit.
 */
function usePriceFields(opts: {
  key: string;
  label: string;
  seedKey: number;
  source: { sale_price: string | null; shipping_profile_id: number | null; platform_fee_percent: string | null };
  isCalculatedFee: boolean;
}) {
  const { sale_price, shipping_profile_id, platform_fee_percent } = opts.source;
  const seed = useMemo<PriceFields>(
    () => ({
      salePrice: sale_price ?? "",
      shippingProfileId: shipping_profile_id != null ? String(shipping_profile_id) : "",
      platformFeePercent: platform_fee_percent ?? "",
    }),
    [sale_price, shipping_profile_id, platform_fee_percent]
  );
  const isCalculatedFee = opts.isCalculatedFee;
  const project = useCallback(
    (v: PriceFields) => (isCalculatedFee ? { salePrice: v.salePrice, shippingProfileId: v.shippingProfileId } : v),
    [isCalculatedFee]
  );

  const copy = useEditableCopy<PriceFields>({
    key: opts.key,
    label: opts.label,
    initial: EMPTY_PRICE_FIELDS,
    seed,
    seedKey: opts.seedKey,
    project,
  });

  return {
    ...copy,
    setSalePrice: (next: string) => copy.setValue((prev) => ({ ...prev, salePrice: next })),
    setShippingProfileId: (next: string) => copy.setValue((prev) => ({ ...prev, shippingProfileId: next })),
    setPlatformFeePercent: (next: string) => copy.setValue((prev) => ({ ...prev, platformFeePercent: next })),
  };
}

function ProductPriceForm({
  product,
  isCalculatedFee,
  profiles,
  feeSource,
  onSaved,
}: {
  product: Product;
  isCalculatedFee: boolean;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const {
    value: { salePrice, shippingProfileId, platformFeePercent },
    setSalePrice,
    setShippingProfileId,
    setPlatformFeePercent,
    isDirty,
    markSaved,
  } = usePriceFields({
    key: "product",
    label: "Pricing",
    seedKey: product.id,
    source: product,
    isCalculatedFee,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      productsApi.update(product.id, {
        sale_price: salePrice || null,
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        platform_fee_percent: platformFeePercent || null,
      }),
    onSuccess: () => {
      markSaved();
      onSaved();
    },
  });

  const margin = computeMargin(
    {
      sale_price: product.sale_price,
      shipping_profile_id: product.shipping_profile_id,
      effective_platform_fee_percent: product.effective_platform_fee_percent,
      cost_per_unit: product.cost_per_unit,
      kitting_cost_per_unit: product.kitting_cost_per_unit,
    },
    profiles,
    feeSource
  );
  const saveStatus = useSaveStatus(saveMutation.status);

  return (
    <>
      <form
        className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          saveMutation.mutate();
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm">Sale price (£)</span>
          <input
            className="w-28 rounded border border-slate-300 px-2 py-1"
            value={salePrice}
            onChange={(e) => setSalePrice(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Shipping profile</span>
          <ShippingProfileSelect
            profiles={profiles}
            value={shippingProfileId}
            onChange={setShippingProfileId}
            feeSource={feeSource}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Platform fee (%)</span>
          {isCalculatedFee ? (
            <span className="w-24 rounded border border-transparent px-2 py-1 text-slate-500">
              {product.effective_platform_fee_percent ? `${Number(product.effective_platform_fee_percent).toFixed(2)}%` : "—"}
            </span>
          ) : (
            <input
              className="w-24 rounded border border-slate-300 px-2 py-1"
              value={platformFeePercent}
              onChange={(e) => setPlatformFeePercent(e.target.value)}
            />
          )}
        </label>
        <SaveButton
          type="submit"
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          Save
        </SaveButton>
        {margin && <MarginSummary margin={margin} />}
      </form>
      <ErrorBanner error={saveMutation.error} />
    </>
  );
}

function VariablePriceGroups({
  product,
  variants,
  attributeIndex,
  isCalculatedFee,
  profiles,
  feeSource,
  onSaved,
}: {
  product: Product;
  variants: Variant[];
  attributeIndex: 1 | 2 | 3;
  isCalculatedFee: boolean;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const groups = new Map<string, Variant[]>();
  for (const v of variants) {
    const value = attributeValue(v, attributeIndex) ?? "(unset)";
    groups.set(value, [...(groups.get(value) ?? []), v]);
  }

  if (groups.size === 0) return <p className="text-sm text-slate-500">No variants to price yet.</p>;

  return (
    <div className="flex flex-col gap-2">
      {Array.from(groups.entries()).map(([value, groupVariants]) => (
        <VariableGroupRow
          key={value}
          label={value}
          variants={groupVariants}
          product={product}
          isCalculatedFee={isCalculatedFee}
          profiles={profiles}
          feeSource={feeSource}
          onSaved={onSaved}
        />
      ))}
    </div>
  );
}

function VariableGroupRow({
  label,
  variants,
  product,
  isCalculatedFee,
  profiles,
  feeSource,
  onSaved,
}: {
  label: string;
  variants: Variant[];
  product: Product;
  isCalculatedFee: boolean;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const first = variants[0];
  const {
    value: { salePrice, shippingProfileId, platformFeePercent },
    setSalePrice,
    setShippingProfileId,
    setPlatformFeePercent,
    isDirty,
    markSaved,
  } = usePriceFields({
    key: `group-${label}`,
    label: `Pricing — ${label}`,
    seedKey: first.id,
    source: first,
    isCalculatedFee,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      Promise.all(
        variants.map((v) =>
          variantsApi.update(v.id, {
            sale_price: salePrice || null,
            shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
            platform_fee_percent: platformFeePercent || null,
          })
        )
      ),
    onSuccess: () => {
      markSaved();
      onSaved();
    },
  });

  const saveStatus = useSaveStatus(saveMutation.status);
  const productProfileName = profiles.find((p) => p.id === product.shipping_profile_id)?.name;

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded bg-white p-3 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault();
        saveMutation.mutate();
      }}
    >
      <div className="flex w-32 flex-col gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-slate-500">
          {variants.length} variant{variants.length === 1 ? "" : "s"}
        </span>
      </div>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Sale price (£)</span>
        <input
          className="w-28 rounded border border-slate-300 px-2 py-1"
          placeholder={product.sale_price ?? ""}
          value={salePrice}
          onChange={(e) => setSalePrice(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Shipping profile</span>
        <ShippingProfileSelect
          profiles={profiles}
          value={shippingProfileId}
          onChange={setShippingProfileId}
          inheritLabel={productProfileName ? `Inherit (${productProfileName})` : "Inherit from product"}
          feeSource={feeSource}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Platform fee (%)</span>
        {isCalculatedFee ? (
          <span className="w-24 px-2 py-1 text-slate-400">calculated</span>
        ) : (
          <input
            className="w-24 rounded border border-slate-300 px-2 py-1"
            placeholder={product.platform_fee_percent ?? ""}
            value={platformFeePercent}
            onChange={(e) => setPlatformFeePercent(e.target.value)}
          />
        )}
      </label>
      <SaveButton
          type="submit"
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
        Save
      </SaveButton>
      <ErrorBanner error={saveMutation.error} />
    </form>
  );
}

function LinePriceTable({
  product,
  variants,
  isCalculatedFee,
  profiles,
  feeSource,
  onSaved,
}: {
  product: Product;
  variants: Variant[];
  isCalculatedFee: boolean;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const guard = useGuard();
  const [showAll, setShowAll] = useState(false);

  if (variants.length === 0) return <p className="text-sm text-slate-500">No variants to price yet.</p>;

  const visible = showAll ? variants : variants.slice(0, INITIAL_LINE_LIMIT);

  return (
    <div className="flex flex-col gap-2">
      {visible.map((v) => (
        <DirtyPath key={v.id} segment={`line-${v.id}`}>
        <LineRow
          variant={v}
          product={product}
          isCalculatedFee={isCalculatedFee}
          profiles={profiles}
          feeSource={feeSource}
          onSaved={onSaved}
        />
        </DirtyPath>
      ))}
      {variants.length > INITIAL_LINE_LIMIT && (
        <button
          onClick={() =>
            // Collapsing unmounts every row past the limit — check them all before it happens.
            guard.attempt(() => setShowAll((v) => !v), {
              prefixes: showAll ? variants.slice(INITIAL_LINE_LIMIT).map((v) => `pricing/line-${v.id}/`) : [],
            })
          }
          className="self-start text-sm text-slate-600 underline"
        >
          {showAll ? "Show less" : `Show all ${variants.length} (${variants.length - INITIAL_LINE_LIMIT} more)`}
        </button>
      )}
    </div>
  );
}

function LineRow({
  variant,
  product,
  isCalculatedFee,
  profiles,
  feeSource,
  onSaved,
}: {
  variant: Variant;
  product: Product;
  isCalculatedFee: boolean;
  profiles: ShippingProfile[];
  feeSource: MarginFeeSource | undefined;
  onSaved: () => void;
}) {
  const {
    value: { salePrice, shippingProfileId, platformFeePercent },
    setSalePrice,
    setShippingProfileId,
    setPlatformFeePercent,
    isDirty,
    markSaved,
  } = usePriceFields({
    key: "price",
    label: `Pricing — ${variant.variant_name}`,
    seedKey: variant.id,
    source: variant,
    isCalculatedFee,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      variantsApi.update(variant.id, {
        sale_price: salePrice || null,
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        platform_fee_percent: platformFeePercent || null,
      }),
    onSuccess: () => {
      markSaved();
      onSaved();
    },
  });

  const saveStatus = useSaveStatus(saveMutation.status);
  const margin = computeMargin(
    {
      sale_price: variant.sale_price ?? product.sale_price,
      shipping_profile_id: variant.effective_shipping_profile_id,
      effective_platform_fee_percent: variant.effective_platform_fee_percent ?? product.effective_platform_fee_percent,
      cost_per_unit: variant.cost_per_unit,
      kitting_cost_per_unit: variant.kitting_cost_per_unit,
    },
    profiles,
    feeSource
  );
  const productProfileName = profiles.find((p) => p.id === product.shipping_profile_id)?.name;

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded bg-white p-3 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault();
        saveMutation.mutate();
      }}
    >
      <span className="w-40 text-sm font-medium">{variant.variant_name}</span>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Sale price (£)</span>
        <input
          className="w-28 rounded border border-slate-300 px-2 py-1"
          placeholder={product.sale_price ?? ""}
          value={salePrice}
          onChange={(e) => setSalePrice(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Shipping profile</span>
        <ShippingProfileSelect
          profiles={profiles}
          value={shippingProfileId}
          onChange={setShippingProfileId}
          inheritLabel={productProfileName ? `Inherit (${productProfileName})` : "Inherit from product"}
          feeSource={feeSource}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm">Platform fee (%)</span>
        {isCalculatedFee ? (
          <span className="w-24 px-2 py-1 text-slate-400">calculated</span>
        ) : (
          <input
            className="w-24 rounded border border-slate-300 px-2 py-1"
            placeholder={product.platform_fee_percent ?? ""}
            value={platformFeePercent}
            onChange={(e) => setPlatformFeePercent(e.target.value)}
          />
        )}
      </label>
      <SaveButton
          type="submit"
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
        Save
      </SaveButton>
      {margin && <MarginSummary margin={margin} />}
      <ErrorBanner error={saveMutation.error} />
    </form>
  );
}
