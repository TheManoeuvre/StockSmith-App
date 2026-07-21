import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  feeConfigApi,
  type FeeBasis,
  type MarginFeeSource,
  type PlatformFeeComponent,
} from "../../api/feeConfig";
import type { ListingPlatform } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

const SOURCE_LABELS: Record<MarginFeeSource, string> = {
  manual: "Manual (flat % you enter per product)",
  etsy: "Etsy calculated (from the components below)",
  ebay: "eBay calculated (from the components below)",
};

const BASIS_LABELS: Record<FeeBasis, string> = {
  sale_price: "% of sale price",
  sale_price_plus_shipping: "% of sale price + shipping",
  fees_subtotal: "% of fees so far (e.g. VAT)",
};

export function MarginFeeSettings() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: ["settings", "margin-fee-config"],
    queryFn: feeConfigApi.getMarginFeeConfig,
  });

  const sourceMutation = useMutation({
    mutationFn: (fee_source: MarginFeeSource) => feeConfigApi.updateMarginFeeConfig(fee_source),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "margin-fee-config"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Margin fee source</p>
        <p className="text-sm text-slate-500">
          Controls how every product's "Platform fee" is calculated for margin display. Applies shop-wide.
        </p>
      </div>
      <select
        className="w-fit rounded border border-slate-300 px-2 py-1 text-sm"
        value={config?.fee_source ?? "manual"}
        onChange={(e) => sourceMutation.mutate(e.target.value as MarginFeeSource)}
      >
        {(Object.keys(SOURCE_LABELS) as MarginFeeSource[]).map((source) => (
          <option key={source} value={source}>
            {SOURCE_LABELS[source]}
          </option>
        ))}
      </select>
      <ErrorBanner error={sourceMutation.error} />

      <FeeComponentTable platform="etsy" label="Etsy fee components" />
      <FeeComponentTable platform="ebay" label="eBay fee components" />
      <p className="text-xs text-slate-400">
        Rates seeded from research in July 2026 — platforms change these periodically, so re-check against
        Etsy's/eBay's own fee pages if margins look off.
      </p>
    </div>
  );
}

function FeeComponentTable({ platform, label }: { platform: ListingPlatform; label: string }) {
  const queryClient = useQueryClient();
  const { data: components } = useQuery({
    queryKey: ["settings", "platform-fee-components", platform],
    queryFn: () => feeConfigApi.listFeeComponents(platform),
  });

  const [newName, setNewName] = useState("");
  const [newBasis, setNewBasis] = useState<FeeBasis>("sale_price_plus_shipping");
  const [newRate, setNewRate] = useState("");
  const [newFixed, setNewFixed] = useState("");

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["settings", "platform-fee-components", platform] });

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<PlatformFeeComponent> }) =>
      feeConfigApi.updateFeeComponent(platform, id, input),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => feeConfigApi.deleteFeeComponent(platform, id),
    onSuccess: invalidate,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      feeConfigApi.createFeeComponent(platform, {
        name: newName,
        basis: newBasis,
        rate_percent: newRate || null,
        fixed_amount: newFixed || null,
        display_order: (components?.length ?? 0) + 1,
      }),
    onSuccess: () => {
      invalidate();
      setNewName("");
      setNewRate("");
      setNewFixed("");
    },
  });

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium">{label}</p>
      <table className="w-full border-collapse bg-white text-left text-xs shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-1.5">Name</th>
            <th className="p-1.5">Applies to</th>
            <th className="p-1.5">Rate %</th>
            <th className="p-1.5">Fixed £</th>
            <th className="p-1.5">Enabled</th>
            <th className="p-1.5" />
          </tr>
        </thead>
        <tbody>
          {components?.map((c) => (
            <tr key={c.id} className="border-b border-slate-100">
              <td className="p-1.5">{c.name}</td>
              <td className="p-1.5">{BASIS_LABELS[c.basis]}</td>
              <td className="p-1.5">{c.rate_percent ?? "—"}</td>
              <td className="p-1.5">{c.fixed_amount ?? "—"}</td>
              <td className="p-1.5">
                <input
                  type="checkbox"
                  checked={c.enabled}
                  onChange={(e) => updateMutation.mutate({ id: c.id, input: { enabled: e.target.checked } })}
                />
              </td>
              <td className="p-1.5">
                <button onClick={() => deleteMutation.mutate(c.id)} className="text-red-600">
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorBanner error={updateMutation.error ?? deleteMutation.error ?? createMutation.error} />
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          createMutation.mutate();
        }}
      >
        <input
          required
          placeholder="Component name"
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <select
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={newBasis}
          onChange={(e) => setNewBasis(e.target.value as FeeBasis)}
        >
          {(Object.keys(BASIS_LABELS) as FeeBasis[]).map((basis) => (
            <option key={basis} value={basis}>
              {BASIS_LABELS[basis]}
            </option>
          ))}
        </select>
        <input
          placeholder="Rate %"
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newRate}
          onChange={(e) => setNewRate(e.target.value)}
        />
        <input
          placeholder="Fixed £"
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newFixed}
          onChange={(e) => setNewFixed(e.target.value)}
        />
        <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm">
          + Add component
        </button>
      </form>
    </div>
  );
}
