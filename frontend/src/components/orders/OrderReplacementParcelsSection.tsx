import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ordersApi } from "../../api/orders";
import type { Order, ReplacementParcel } from "../../api/types";
import { Badge } from "../common/Badge";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { CopyButton } from "../common/CopyButton";
import { ErrorBanner } from "../common/ErrorBanner";
import { Th } from "../common/ListTable";
import { formatDayMonth } from "../../lib/format";
import { formatMoney } from "../../lib/money";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ReplacementParcelModal, invalidateAfterParcelChange } from "./ReplacementParcelModal";
import { REASON_LABELS } from "./replacementParcels";

/**
 * Everything sent against this order AFTER the original shipment — kept visually apart
 * from the order lines above it (amber block, own tables) because these are not sale
 * demand: they cost money without earning any, and they never touch allocation.
 *
 * Renders only the "Record replacement parcel" button until there is a parcel to show.
 * A sync-created parcel (a second marketplace label the sync noticed) sits here flagged
 * "Needs completing" until the user says what went in it.
 */
export function OrderReplacementParcelsSection({ order }: { order: Order }) {
  const queryClient = useQueryClient();
  const [modal, setModal] = useState<{ parcel?: ReplacementParcel } | null>(null);
  const [deleting, setDeleting] = useState<ReplacementParcel | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (parcelId: number) => ordersApi.deleteReplacementParcel(parcelId),
    onSuccess: () => {
      invalidateAfterParcelChange(queryClient, order.id);
      setDeleting(null);
    },
  });

  const parcels = order.replacement_parcels;
  const canRecord = order.status !== "cancelled";
  const recordButton = (
    <button
      type="button"
      onClick={() => setModal({})}
      disabled={!canRecord}
      title={canRecord ? undefined : "A cancelled order can't have parcels sent against it"}
      className="w-fit rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:text-slate-400"
    >
      Record replacement parcel
    </button>
  );

  return (
    <>
      {parcels.length === 0 ? (
        recordButton
      ) : (
        <section
          aria-label="Replacement parcels"
          className="flex flex-col gap-3 rounded-lg border border-amber-200 bg-amber-50/40 p-4"
        >
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-amber-900">
              Replacement parcels
              <span className="ml-2 text-xs font-normal text-amber-700">
                sent after the original shipment · not part of the order lines above
              </span>
            </h2>
            {recordButton}
          </div>
          {parcels.map((parcel) => (
            <ParcelCard
              key={parcel.id}
              parcel={parcel}
              order={order}
              onEdit={() => setModal({ parcel })}
              onDelete={() => setDeleting(parcel)}
            />
          ))}
          <ErrorBanner error={deleteMutation.error} />
        </section>
      )}

      {modal && <ReplacementParcelModal order={order} parcel={modal.parcel} onClose={() => setModal(null)} />}

      <ConfirmDialog
        open={deleting != null}
        title="Delete this replacement parcel?"
        body={
          deleting && deleting.items.length > 0
            ? `${describeRestock(deleting)} will be returned to stock. ${
                deleting.postage_charge ? "The marketplace label stays recorded against the order." : ""
              }`
            : "Nothing to restock. Any marketplace label it was linked to stays recorded against the order."
        }
        confirmLabel="Delete parcel"
        busy={deleteMutation.isPending}
        onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </>
  );
}

function describeRestock(parcel: ReplacementParcel): string {
  const products = parcel.items.filter((i) => i.product_id != null).reduce((sum, i) => sum + Number(i.qty), 0);
  const materials = parcel.items.filter((i) => i.material_id != null).length;
  const parts = [];
  if (products > 0) parts.push(`${products} product unit${products === 1 ? "" : "s"}`);
  if (materials > 0) parts.push(`${materials} packaging item${materials === 1 ? "" : "s"}`);
  return parts.join(" and ");
}

function ParcelCard({
  parcel,
  order,
  onEdit,
  onDelete,
}: {
  parcel: ReplacementParcel;
  order: Order;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const currency = order.currency;
  const charge = parcel.postage_charge;
  return (
    <div
      id={`replacement-parcel-${parcel.id}`}
      className="flex flex-col gap-2 rounded-md border border-amber-200/80 bg-white p-3 text-sm"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-slate-900">{REASON_LABELS[parcel.reason]}</span>
        {parcel.needs_review && <Badge className="bg-amber-100 text-amber-800">Needs completing</Badge>}
        <span className="text-xs text-slate-500">Sent {formatDayMonth(parcel.sent_at)}</span>
        <span className="text-xs text-slate-500">
          Postage{" "}
          <span className="font-medium text-slate-700">
            {parcel.effective_postage != null ? formatMoney(parcel.effective_postage, currency) : "—"}
          </span>
          {charge && (
            <span className="text-slate-400">
              {" "}
              · {order.platform ? PLATFORM_LABELS[order.platform] : "marketplace"} label #{charge.sequence}
            </span>
          )}
        </span>
        {parcel.tracking_number && (
          <span className="flex items-center gap-1 text-xs text-slate-500">
            {parcel.carrier ? `${parcel.carrier} · ` : ""}
            <span className="font-mono">{parcel.tracking_number}</span>
            <CopyButton value={parcel.tracking_number} label="Copy tracking number" />
          </span>
        )}
        <span className="ml-auto flex gap-3 text-xs">
          <button type="button" onClick={onEdit} className="text-slate-600 underline">
            {parcel.needs_review ? "Complete" : "Edit"}
          </button>
          <button type="button" onClick={onDelete} className="text-red-600 underline">
            Delete
          </button>
        </span>
      </div>
      {parcel.items.length > 0 ? (
        <table className="w-full border-collapse text-left text-[12.5px]">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/60">
              <Th>Item</Th>
              <Th align="right">Qty</Th>
              <Th align="right">Unit cost</Th>
              <Th align="right">Cost</Th>
            </tr>
          </thead>
          <tbody>
            {parcel.items.map((item) => (
              <tr key={item.id} className="border-b border-slate-100">
                <td className="p-2">
                  {item.material_id != null ? (
                    <>
                      {item.material_name}
                      <span className="ml-1 text-xs text-slate-400">packaging</span>
                    </>
                  ) : (
                    <>
                      {item.product_name}
                      {item.variant_name ? ` — ${item.variant_name}` : ""}
                    </>
                  )}
                </td>
                <td className="p-2 text-right tabular-nums">
                  {Number(item.qty)}
                  {item.material_unit && item.material_unit !== "each" ? ` ${item.material_unit}` : ""}
                </td>
                <td className="p-2 text-right tabular-nums">{formatMoney(item.unit_cost_snapshot, currency)}</td>
                <td className="p-2 text-right tabular-nums">{formatMoney(item.line_cost, currency)}</td>
              </tr>
            ))}
          </tbody>
          {parcel.items_cost != null && (
            <tfoot>
              <tr>
                <td colSpan={3} className="p-2 text-right text-xs text-slate-500">
                  Cost of goods
                </td>
                <td className="p-2 text-right font-medium tabular-nums">{formatMoney(parcel.items_cost, currency)}</td>
              </tr>
            </tfoot>
          )}
        </table>
      ) : (
        <p className="text-xs text-amber-700">
          {parcel.needs_review
            ? "Detected from a marketplace label — what was sent hasn't been recorded yet, so stock and cost of goods don't reflect it."
            : "Postage only — no items recorded."}
        </p>
      )}
      {parcel.notes && <p className="text-xs text-slate-500">{parcel.notes}</p>}
    </div>
  );
}
