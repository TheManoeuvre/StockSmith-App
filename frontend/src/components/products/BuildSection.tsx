import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { buildsApi, productsApi } from "../../api/products";
import { ErrorBanner } from "../common/ErrorBanner";

export function BuildSection({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId),
  });
  const { data: history } = useQuery({
    queryKey: ["products", productId, "builds"],
    queryFn: () => productsApi.listBuilds(productId),
  });

  // A product whose variants are all disabled is treated as if it had none — the build
  // form falls back to the bare product's own SKU/BOM/stock rather than forcing a
  // (disabled) variant to be picked. Build history still shows the variant column
  // whenever any variant row ever existed, active or not, so past builds keep their label.
  const activeVariants = (variants ?? []).filter((v) => v.is_active);
  const hasActiveVariants = activeVariants.length > 0;
  const hasAnyVariants = (variants?.length ?? 0) > 0;

  const [variantId, setVariantId] = useState<number | "">("");
  const [qty, setQty] = useState("1");
  const [notes, setNotes] = useState("");

  const buildMutation = useMutation({
    mutationFn: () =>
      buildsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(variantId) : null,
        qty_built: Number(qty),
        notes: notes || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "builds"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      setQty("1");
      setNotes("");
    },
  });

  const variantName = (id: number | null) => variants?.find((v) => v.id === id)?.variant_name ?? "—";

  return (
    <div className="flex flex-col gap-3">
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
            required
            type="number"
            min={1}
            className="w-24 rounded border border-slate-300 px-2 py-1"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Notes</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
          Record build
        </button>
      </form>
      <ErrorBanner error={buildMutation.error} />

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Date</th>
            {hasAnyVariants && <th className="p-2">Variant</th>}
            <th className="p-2">Qty built</th>
            <th className="p-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          {history?.map((b) => (
            <tr key={b.id} className="border-b border-slate-100">
              <td className="p-2">{new Date(b.built_at).toLocaleString()}</td>
              {hasAnyVariants && <td className="p-2">{variantName(b.variant_id)}</td>}
              <td className="p-2">{b.qty_built}</td>
              <td className="p-2">{b.notes ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
