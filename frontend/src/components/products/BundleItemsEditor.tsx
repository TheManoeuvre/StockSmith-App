import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { productsApi } from "../../api/products";
import type { BundleItem } from "../../api/types";
import { ProductSelect } from "./ProductSelect";
import { ErrorBanner } from "../common/ErrorBanner";

export function BundleItemsEditor({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: bundleItems } = useQuery({
    queryKey: ["products", productId, "bundle-items"],
    queryFn: () => productsApi.getBundleItems(productId),
  });
  const { data: products } = useQuery({ queryKey: ["products"], queryFn: productsApi.list });

  const [lines, setLines] = useState<BundleItem[]>([]);

  useEffect(() => {
    if (bundleItems) setLines(bundleItems.map((l) => ({ component_product_id: l.component_product_id, qty: l.qty })));
  }, [bundleItems]);

  // A bundle's components can't themselves be bundles, and can't be the bundle itself.
  const availableProducts = (products ?? []).filter((p) => !p.is_bundle && p.id !== productId);

  const saveMutation = useMutation({
    mutationFn: () => productsApi.replaceBundleItems(productId, lines),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "bundle-items"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const updateLine = (index: number, patch: Partial<BundleItem>) => {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => setLines((prev) => prev.filter((_, i) => i !== index));

  const addLine = () => {
    const firstUnused = availableProducts.find((p) => !lines.some((l) => l.component_product_id === p.id));
    if (!firstUnused) return;
    setLines((prev) => [...prev, { component_product_id: firstUnused.id, qty: 1 }]);
  };

  return (
    <div className="flex flex-col gap-2">
      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Component product</th>
            <th className="p-2">Qty</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="border-b border-slate-100">
              <td className="p-2">
                <ProductSelect
                  products={availableProducts}
                  value={line.component_product_id}
                  onChange={(component_product_id) => updateLine(i, { component_product_id })}
                />
              </td>
              <td className="p-2">
                <input
                  type="number"
                  min={1}
                  className="w-20 rounded border border-slate-300 px-2 py-1"
                  value={line.qty}
                  onChange={(e) => updateLine(i, { qty: Number(e.target.value) })}
                />
              </td>
              <td className="p-2">
                <button onClick={() => removeLine(i)} className="text-red-600">
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex gap-2">
        <button onClick={addLine} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add component
        </button>
        <button onClick={() => saveMutation.mutate()} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
          Save bundle
        </button>
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
