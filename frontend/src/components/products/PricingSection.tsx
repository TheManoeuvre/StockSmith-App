import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { feeConfigApi, type MarginFeeSource } from "../../api/feeConfig";
import { productsApi } from "../../api/products";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import { variantsApi } from "../../api/variants";
import type { PricingMode, Product, ShippingProfile, Variant } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";

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
): { profit: number; marginPercent: number } | null {
  if (!inputs.sale_price) return null;
  const salePrice = Number(inputs.sale_price);
  const cost = inputs.cost_per_unit ? Number(inputs.cost_per_unit) : 0;
  const profile = profiles.find((p) => p.id === inputs.shipping_profile_id);
  const shipping = profile ? shippingCostForFeeSource(profile, feeSource) : 0;
  const fee = (salePrice * (inputs.effective_platform_fee_percent ? Number(inputs.effective_platform_fee_percent) : 0)) / 100;
  const profit = salePrice - cost - shipping - fee;
  const marginPercent = salePrice !== 0 ? (profit / salePrice) * 100 : 0;
  return { profit, marginPercent };
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
    queryFn: shippingProfilesApi.list,
  });
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
            onChange={(e) => modeMutation.mutate(e.target.value as PricingMode)}
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
              onChange={(e) => attributeMutation.mutate(Number(e.target.value))}
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
                <td className="p-2">£{Number(h.cost_per_unit).toFixed(4)}</td>
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
  const [salePrice, setSalePrice] = useState("");
  const [shippingProfileId, setShippingProfileId] = useState("");
  const [platformFeePercent, setPlatformFeePercent] = useState("");

  useEffect(() => {
    setSalePrice(product.sale_price ?? "");
    setShippingProfileId(product.shipping_profile_id != null ? String(product.shipping_profile_id) : "");
    setPlatformFeePercent(product.platform_fee_percent ?? "");
  }, [product.id, product.sale_price, product.shipping_profile_id, product.platform_fee_percent]);

  const saveMutation = useMutation({
    mutationFn: () =>
      productsApi.update(product.id, {
        sale_price: salePrice || null,
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        platform_fee_percent: platformFeePercent || null,
      }),
    onSuccess: onSaved,
  });

  const margin = computeMargin(
    {
      sale_price: product.sale_price,
      shipping_profile_id: product.shipping_profile_id,
      effective_platform_fee_percent: product.effective_platform_fee_percent,
      cost_per_unit: product.cost_per_unit,
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
        <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
          Save
        </button>
        <SaveIndicator status={saveStatus} />
        {margin && (
          <span className="text-sm">
            Profit: <strong>£{margin.profit.toFixed(2)}</strong> · Margin: <strong>{margin.marginPercent.toFixed(1)}%</strong>
          </span>
        )}
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
  const [salePrice, setSalePrice] = useState("");
  const [shippingProfileId, setShippingProfileId] = useState("");
  const [platformFeePercent, setPlatformFeePercent] = useState("");

  useEffect(() => {
    setSalePrice(first.sale_price ?? "");
    setShippingProfileId(first.shipping_profile_id != null ? String(first.shipping_profile_id) : "");
    setPlatformFeePercent(first.platform_fee_percent ?? "");
  }, [first.id, first.sale_price, first.shipping_profile_id, first.platform_fee_percent]);

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
    onSuccess: onSaved,
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
      <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
        Save
      </button>
      <SaveIndicator status={saveStatus} />
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
  const [showAll, setShowAll] = useState(false);

  if (variants.length === 0) return <p className="text-sm text-slate-500">No variants to price yet.</p>;

  const visible = showAll ? variants : variants.slice(0, INITIAL_LINE_LIMIT);

  return (
    <div className="flex flex-col gap-2">
      {visible.map((v) => (
        <LineRow
          key={v.id}
          variant={v}
          product={product}
          isCalculatedFee={isCalculatedFee}
          profiles={profiles}
          feeSource={feeSource}
          onSaved={onSaved}
        />
      ))}
      {variants.length > INITIAL_LINE_LIMIT && (
        <button onClick={() => setShowAll((s) => !s)} className="self-start text-sm text-slate-600 underline">
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
  const [salePrice, setSalePrice] = useState("");
  const [shippingProfileId, setShippingProfileId] = useState("");
  const [platformFeePercent, setPlatformFeePercent] = useState("");

  useEffect(() => {
    setSalePrice(variant.sale_price ?? "");
    setShippingProfileId(variant.shipping_profile_id != null ? String(variant.shipping_profile_id) : "");
    setPlatformFeePercent(variant.platform_fee_percent ?? "");
  }, [variant.id, variant.sale_price, variant.shipping_profile_id, variant.platform_fee_percent]);

  const saveMutation = useMutation({
    mutationFn: () =>
      variantsApi.update(variant.id, {
        sale_price: salePrice || null,
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        platform_fee_percent: platformFeePercent || null,
      }),
    onSuccess: onSaved,
  });

  const saveStatus = useSaveStatus(saveMutation.status);
  const margin = computeMargin(
    {
      sale_price: variant.sale_price ?? product.sale_price,
      shipping_profile_id: variant.effective_shipping_profile_id,
      effective_platform_fee_percent: variant.effective_platform_fee_percent ?? product.effective_platform_fee_percent,
      cost_per_unit: variant.cost_per_unit,
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
      <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
        Save
      </button>
      <SaveIndicator status={saveStatus} />
      {margin && (
        <span className="text-sm">
          Profit: <strong>£{margin.profit.toFixed(2)}</strong> · Margin: <strong>{margin.marginPercent.toFixed(1)}%</strong>
        </span>
      )}
      <ErrorBanner error={saveMutation.error} />
    </form>
  );
}
