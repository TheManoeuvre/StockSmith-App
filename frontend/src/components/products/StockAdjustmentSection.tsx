import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { productsApi, stockAdjustmentsApi } from "../../api/products";
import { ErrorBanner } from "../common/ErrorBanner";

export function StockAdjustmentSection({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId),
  });
  const { data: history } = useQuery({
    queryKey: ["products", productId, "stock-adjustments"],
    queryFn: () => productsApi.listStockAdjustments(productId),
  });

  // Same "all variants disabled == no variants" fallback as BuildSection: the form
  // targets the bare product's own current_stock rather than forcing a disabled
  // variant to be picked.
  const activeVariants = (variants ?? []).filter((v) => v.is_active);
  const hasActiveVariants = activeVariants.length > 0;
  const hasAnyVariants = (variants?.length ?? 0) > 0;

  const [variantId, setVariantId] = useState<number | "">("");
  const [mode, setMode] = useState<"adjust" | "set">("adjust");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");

  const adjustMutation = useMutation({
    mutationFn: () =>
      stockAdjustmentsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(variantId) : null,
        mode,
        value: Number(value),
        reason,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "stock-adjustments"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setValue("");
      setReason("");
    },
  });

  const variantName = (id: number | null) => variants?.find((v) => v.id === id)?.variant_name ?? "—";

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-md font-semibold">Stock adjustment</h3>
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
          <span className="text-sm">Mode</span>
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={mode}
            onChange={(e) => setMode(e.target.value as "adjust" | "set")}
          >
            <option value="adjust">Adjust (+/-)</option>
            <option value="set">Set exact amount</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">{mode === "set" ? "Set to" : "Adjust by"}</span>
          <input
            required
            type="number"
            className="w-24 rounded border border-slate-300 px-2 py-1"
            placeholder={mode === "set" ? "e.g. 53" : "e.g. -5 or 10"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Reason</span>
          <input
            required
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="Breakage, recount, …"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
          Save
        </button>
      </form>
      <ErrorBanner error={adjustMutation.error} />

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Date</th>
            {hasAnyVariants && <th className="p-2">Variant</th>}
            <th className="p-2">Qty</th>
            <th className="p-2">Reason</th>
          </tr>
        </thead>
        <tbody>
          {history?.map((a) => (
            <tr key={a.id} className="border-b border-slate-100">
              <td className="p-2">{new Date(a.created_at).toLocaleString()}</td>
              {hasAnyVariants && <td className="p-2">{variantName(a.variant_id)}</td>}
              <td className="p-2">
                {a.mode === "set" ? (
                  <>
                    Set to {a.target_qty}{" "}
                    <span className="text-xs text-slate-400">
                      (Δ {a.qty_delta > 0 ? "+" : ""}
                      {a.qty_delta})
                    </span>
                  </>
                ) : (
                  <>
                    {a.qty_delta > 0 ? "+" : ""}
                    {a.qty_delta}
                  </>
                )}
              </td>
              <td className="p-2">{a.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
