import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { buildsApi, productsApi, stockAdjustmentsApi } from "../../api/products";
import { materialsApi } from "../../api/materials";
import type { ProductStockEvent } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

const EVENT_LABELS: Record<ProductStockEvent["event_type"], string> = {
  build_success: "Build",
  build_failed: "Failed build",
  adjustment: "Adjustment",
  order_fulfillment: "Order shipped",
};

export function StockSection({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
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

  const [variantId, setVariantId] = useState<number | "">("");
  const [qtyBuilt, setQtyBuilt] = useState("1");
  const [qtyFailed, setQtyFailed] = useState("0");
  const [notes, setNotes] = useState("");
  const [consumption, setConsumption] = useState<Record<number, boolean>>({});

  const selectedVariant = variants?.find((v) => v.id === variantId);
  const resolvedBom = hasActiveVariants ? selectedVariant?.effective_bom ?? [] : bom ?? [];
  const materialById = useMemo(() => new Map((materials ?? []).map((m) => [m.id, m])), [materials]);
  const qtyFailedNum = Number(qtyFailed) || 0;

  const consumptionFor = (materialId: number) =>
    consumption[materialId] ?? materialById.get(materialId)?.category === "filament";

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
      setQtyBuilt("1");
      setQtyFailed("0");
      setNotes("");
      setConsumption({});
    },
  });

  const [adjVariantId, setAdjVariantId] = useState<number | "">("");
  const [adjMode, setAdjMode] = useState<"adjust" | "set">("adjust");
  const [adjValue, setAdjValue] = useState("");
  const [adjReason, setAdjReason] = useState("");

  const adjustMutation = useMutation({
    mutationFn: () =>
      stockAdjustmentsApi.create({
        product_id: productId,
        variant_id: hasActiveVariants ? Number(adjVariantId) : null,
        mode: adjMode,
        value: Number(adjValue),
        reason: adjReason,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "stock-history"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setAdjValue("");
      setAdjReason("");
    },
  });

  const variantName = (id: number | null) => variants?.find((v) => v.id === id)?.variant_name ?? "—";

  const eventDetail = (e: ProductStockEvent) => {
    switch (e.event_type) {
      case "build_success":
        return e.build_qty_failed ? `${e.build_qty_built} built (${e.build_qty_failed} also failed this run)` : `${e.build_qty_built} built`;
      case "build_failed":
        return `${e.build_qty_failed} failed`;
      case "adjustment":
        return e.adjustment_mode === "set" ? `Set to ${e.adjustment_target_qty}` : e.reason;
      case "order_fulfillment":
        return e.order_external_order_id ? `Order ${e.order_external_order_id}` : `Order #${e.order_id}`;
      default:
        return "—";
    }
  };

  return (
    <div className="flex flex-col gap-6">
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
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Record
          </button>
        </form>

        {qtyFailedNum > 0 && resolvedBom.length > 0 && (
          <div className="rounded bg-white p-4 text-sm shadow-sm">
            <p className="mb-2 text-slate-500">
              Which materials were consumed for the {qtyFailedNum} failed unit(s)? Filament is checked by default —
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
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-sm">Reason</span>
            <input
              required
              className="rounded border border-slate-300 px-2 py-1"
              placeholder="Breakage, recount, …"
              value={adjReason}
              onChange={(e) => setAdjReason(e.target.value)}
            />
          </label>
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Save
          </button>
        </form>
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
              <th className="p-2">Qty change</th>
              <th className="p-2">Balance</th>
              <th className="p-2">Detail</th>
            </tr>
          </thead>
          <tbody>
            {history?.map((e) => (
              <tr key={e.id} className="border-b border-slate-100">
                <td className="p-2">{new Date(e.created_at).toLocaleString()}</td>
                <td className="p-2">{EVENT_LABELS[e.event_type]}</td>
                {hasAnyVariants && <td className="p-2">{variantName(e.variant_id)}</td>}
                <td className="p-2">
                  {e.qty_delta > 0 ? "+" : ""}
                  {e.qty_delta}
                </td>
                <td className="p-2">{e.running_balance}</td>
                <td className="p-2">{eventDetail(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
