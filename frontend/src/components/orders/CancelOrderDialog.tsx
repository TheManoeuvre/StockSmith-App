import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ordersApi, type LineCancellationDecision, type ReturnDisposition } from "../../api/orders";
import { ErrorBanner } from "../common/ErrorBanner";

// Per-line scrap/return-to-stock prompt for cancelling or returning an order — see
// docs/plan-marketplace-integrations.md Section 4. Replaces the old one-click cancel,
// which silently returned everything to stock and flatly refused anything with shipped
// units.
export function CancelOrderDialog({
  orderId,
  onClose,
  onCancelled,
}: {
  orderId: number;
  onClose: () => void;
  onCancelled: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: preview, isLoading } = useQuery({
    queryKey: ["orders", orderId, "cancellation-preview"],
    queryFn: () => ordersApi.cancellationPreview(orderId),
  });
  const [decisions, setDecisions] = useState<Record<number, LineCancellationDecision>>({});
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!preview) return;
    const initial: Record<number, LineCancellationDecision> = {};
    for (const line of preview.lines) {
      initial[line.order_line_id] = {
        order_line_id: line.order_line_id,
        product_disposition: line.default_product_disposition,
        kitting_disposition: line.shipped_qty > 0 ? line.default_kitting_disposition : undefined,
      };
    }
    setDecisions(initial);
  }, [preview]);

  const setProductDisposition = (lineId: number, value: ReturnDisposition) =>
    setDecisions((d) => ({ ...d, [lineId]: { ...d[lineId], order_line_id: lineId, product_disposition: value } }));
  const setKittingDisposition = (lineId: number, value: ReturnDisposition) =>
    setDecisions((d) => ({ ...d, [lineId]: { ...d[lineId], order_line_id: lineId, kitting_disposition: value } }));

  const cancelMutation = useMutation({
    mutationFn: () => ordersApi.cancel(orderId, { line_decisions: Object.values(decisions), reason: reason || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      onCancelled();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded bg-white shadow-lg">
        <div className="border-b border-slate-200 p-4">
          <h2 className="text-lg font-semibold">Cancel order</h2>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
          {preview && preview.lines.length === 0 && (
            <p className="text-sm text-slate-600">
              Nothing allocated or shipped on this order — it'll just be marked cancelled.
            </p>
          )}
          <div className="flex flex-col gap-4">
            {preview?.lines.map((line) => (
              <div key={line.order_line_id} className="rounded border border-slate-200 p-3">
                <p className="font-medium">
                  {line.product_name ?? `Product #${line.product_id}`}
                  {line.variant_name ? ` — ${line.variant_name}` : ""}
                </p>

                {line.pending_qty > 0 && (
                  <div className="mt-2 text-sm">
                    <p className="text-slate-500">{line.pending_qty} unit(s) reserved, not yet shipped</p>
                    <DispositionRadio
                      name={`product-pending-${line.order_line_id}`}
                      value={decisions[line.order_line_id]?.product_disposition}
                      onChange={(v) => setProductDisposition(line.order_line_id, v)}
                      returnLabel="Return to stock — release the reservation (default)"
                      scrapLabel="Scrap — release the reservation and write off the stock"
                    />
                  </div>
                )}

                {line.shipped_qty > 0 && (
                  <div className="mt-2 text-sm">
                    <p className="text-slate-500">{line.shipped_qty} unit(s) already shipped</p>
                    <DispositionRadio
                      name={`product-shipped-${line.order_line_id}`}
                      value={decisions[line.order_line_id]?.product_disposition}
                      onChange={(v) => setProductDisposition(line.order_line_id, v)}
                      returnLabel="Return to stock — resellable (default)"
                      scrapLabel="Scrap — damaged/unsellable, no stock credit"
                    />
                    {line.kitting_materials.length > 0 && (
                      <div className="mt-2 rounded bg-slate-50 p-2">
                        <p className="text-xs text-slate-500">
                          Packaging: {line.kitting_materials.map((m) => m.material_name).join(", ")}
                        </p>
                        <DispositionRadio
                          name={`kitting-${line.order_line_id}`}
                          value={decisions[line.order_line_id]?.kitting_disposition}
                          onChange={(v) => setKittingDisposition(line.order_line_id, v)}
                          returnLabel="Return to stock — came back reusable"
                          scrapLabel="Scrap — already consumed, can't be un-used (default)"
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          <label className="mt-4 flex flex-col gap-1 text-sm">
            <span className="text-slate-500">Reason (optional)</span>
            <input
              className="rounded border border-slate-300 px-2 py-1.5"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <ErrorBanner error={cancelMutation.error} />
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 p-4">
          <button onClick={onClose} className="rounded border border-slate-300 px-4 py-2 text-sm">
            Back
          </button>
          <button
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending || isLoading}
            className="rounded bg-red-600 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {cancelMutation.isPending ? "Cancelling…" : "Confirm cancellation"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DispositionRadio({
  name,
  value,
  onChange,
  returnLabel,
  scrapLabel,
}: {
  name: string;
  value: ReturnDisposition | undefined;
  onChange: (value: ReturnDisposition) => void;
  returnLabel: string;
  scrapLabel: string;
}) {
  return (
    <div className="mt-1 flex flex-col gap-1">
      <label className="flex items-center gap-2">
        <input
          type="radio"
          name={name}
          checked={value === "return_to_stock"}
          onChange={() => onChange("return_to_stock")}
        />
        {returnLabel}
      </label>
      <label className="flex items-center gap-2">
        <input type="radio" name={name} checked={value === "scrap"} onChange={() => onChange("scrap")} />
        {scrapLabel}
      </label>
    </div>
  );
}
