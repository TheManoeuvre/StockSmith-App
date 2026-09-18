import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import { ordersApi, type ReplacementParcelCreateInput, type ReplacementParcelUpdateInput } from "../../api/orders";
import { productsApi } from "../../api/products";
import type { Order, Product, ReplacementParcel, ReplacementParcelReason } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { SegmentedControl } from "../common/SegmentedControl";
import { MaterialSelect } from "../materials/MaterialSelect";
import { formatMoney } from "../../lib/money";
import { formatDayMonth } from "../../lib/format";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { REASON_LABELS, SELECTABLE_REASONS } from "./replacementParcels";

interface ProductRow {
  product_id: number;
  variant_id: number | null;
  qty: string;
}

interface MaterialRow {
  material_id: number;
  qty: string;
}

type PostageMode = "manual" | "label";

/** Query keys every parcel mutation invalidates — the order itself, the list and counts
 *  (a needs-review parcel moves the order between tabs), and stock on both sides. */
export function invalidateAfterParcelChange(
  queryClient: ReturnType<typeof useQueryClient>,
  orderId: number,
) {
  queryClient.invalidateQueries({ queryKey: ["orders"] });
  queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
  queryClient.invalidateQueries({ queryKey: ["order-counts"] });
  queryClient.invalidateQueries({ queryKey: ["products"] });
  queryClient.invalidateQueries({ queryKey: ["materials"] });
  queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  queryClient.invalidateQueries({ queryKey: ["notifications"] });
}

/**
 * Records a replacement parcel — or, in `edit` mode, fills in a sync-created one.
 *
 * Create: products and packaging are pre-filled from what the order originally shipped
 * (one row per line and one of each packaging material it used, all qty 1), since a
 * resend is nearly always a repeat of the original. Saving deducts stock on the spot.
 *
 * Edit: reason, postage, tracking and notes only — items on a recorded parcel can't be
 * changed, so stock effects stay one forward and one reverse path.
 *
 * Completing a sync-created placeholder (a parcel with a label but no items) is a create,
 * not an edit: the item rows are shown, and the save is one create call carrying the
 * placeholder's label — the backend swaps the placeholder out for the real parcel in the
 * same transaction (order_parcels.create_manual_parcel).
 */
export function ReplacementParcelModal({
  order,
  parcel,
  onClose,
}: {
  order: Order;
  /** Present in edit mode. */
  parcel?: ReplacementParcel;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const editing = parcel != null;
  // A sync-created parcel with nothing in it is "completed" by recording items, which
  // means creating a real parcel in its place (see the docstring).
  const completingPlaceholder = editing && parcel.source === "sync" && parcel.items.length === 0;
  const itemsEditable = !editing || completingPlaceholder;

  const { data: products } = useQuery({ queryKey: ["products"], queryFn: productsApi.list, enabled: itemsEditable });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list, enabled: itemsEditable });
  const { data: kitting } = useQuery({
    queryKey: ["orders", order.id, "kitting-overrides"],
    queryFn: () => ordersApi.getKittingOverrides(order.id),
    enabled: itemsEditable,
  });

  const [reason, setReason] = useState<ReplacementParcelReason>(
    parcel && parcel.reason !== "unspecified" ? parcel.reason : "missing_from_order",
  );
  const [productRows, setProductRows] = useState<ProductRow[] | null>(null);
  const [materialRows, setMaterialRows] = useState<MaterialRow[] | null>(null);
  const [tracking, setTracking] = useState(parcel?.tracking_number ?? "");
  const [carrier, setCarrier] = useState(parcel?.carrier ?? "");
  const [notes, setNotes] = useState(parcel?.source === "sync" ? "" : (parcel?.notes ?? ""));
  const [postageCost, setPostageCost] = useState(parcel?.postage_cost ?? "");

  // Labels the sync has found that no parcel has claimed yet — plus, in edit mode, the one
  // this parcel already holds.
  const linkableCharges = order.postage_charges.filter(
    (c) => c.sequence >= 2 && (c.replacement_parcel_id == null || c.replacement_parcel_id === parcel?.id),
  );
  const [postageMode, setPostageMode] = useState<PostageMode>(
    parcel?.postage_charge != null || (!editing && linkableCharges.length > 0) ? "label" : "manual",
  );
  const [chargeId, setChargeId] = useState<number | null>(parcel?.postage_charge?.id ?? linkableCharges[0]?.id ?? null);

  // Seed the item rows once the data they depend on is in.
  const seededProducts =
    productRows ??
    (itemsEditable
      ? dedupe(
          order.lines
            .filter((l) => l.product_id != null)
            .map((l) => ({ product_id: l.product_id as number, variant_id: l.variant_id, qty: "1" })),
        )
      : []);
  // One of each packaging material the order used, not the order's full requirement: a
  // replacement is one parcel, however many units the original shipped in (the same
  // reasoning as auto_apply_multiunit_kitting_override).
  const seededMaterials =
    materialRows ??
    (itemsEditable && kitting
      ? kitting.lines
          .filter((l) => Number(l.effective_qty) > 0)
          .map((l) => ({ material_id: l.material_id, qty: "1" }))
      : []);

  const mutation = useMutation({
    mutationFn: async () => {
      const chargeForSave = postageMode === "label" ? chargeId : null;
      const postageForSave = postageMode === "manual" ? postageCost.trim() || null : null;
      if (!itemsEditable) {
        const patch: ReplacementParcelUpdateInput = {
          reason,
          postage_cost: postageForSave,
          postage_charge_id: chargeForSave,
          tracking_number: tracking.trim() || null,
          carrier: carrier.trim() || null,
          notes: notes.trim() || null,
          needs_review: false,
        };
        return ordersApi.updateReplacementParcel(parcel.id, patch);
      }
      const input: ReplacementParcelCreateInput = {
        reason,
        postage_cost: postageForSave,
        tracking_number: tracking.trim() || null,
        carrier: carrier.trim() || null,
        notes: notes.trim() || null,
        sent_at: completingPlaceholder ? parcel.sent_at : null,
        items: [
          ...seededProducts.map((r) => ({ product_id: r.product_id, variant_id: r.variant_id, qty: r.qty })),
          ...seededMaterials.map((r) => ({ material_id: r.material_id, qty: r.qty })),
        ],
      };
      if (completingPlaceholder) {
        // Always the placeholder's own label (postage mode is pinned to it above) — the
        // backend retires the placeholder for us.
        input.postage_charge_id = parcel.postage_charge?.id ?? null;
        input.postage_cost = null;
      } else {
        input.postage_charge_id = chargeForSave;
      }
      return ordersApi.createReplacementParcel(order.id, input);
    },
    onSuccess: () => {
      invalidateAfterParcelChange(queryClient, order.id);
      onClose();
    },
  });

  const hasItems = seededProducts.length + seededMaterials.length > 0;
  const hasPostage = postageMode === "label" ? chargeId != null : postageCost.trim() !== "";
  const itemsValid =
    seededProducts.every((r) => Number.isInteger(Number(r.qty)) && Number(r.qty) > 0) &&
    seededMaterials.every((r) => Number(r.qty) > 0);
  const canSave = itemsValid && (itemsEditable ? hasItems || hasPostage : true) && !mutation.isPending;

  const title = completingPlaceholder
    ? "Complete replacement parcel"
    : editing
      ? "Edit replacement parcel"
      : "Record replacement parcel";

  return (
    <Modal
      title={title}
      subtitle={
        itemsEditable
          ? "Stock for everything listed here leaves the moment you save — this parcel has already gone."
          : "Items on a recorded parcel can't be changed; delete it and record it again if they're wrong."
      }
      maxWidth="max-w-2xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canSave}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {mutation.isPending ? "Saving…" : editing ? "Save" : "Record parcel"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4 text-sm">
        <label className="flex items-center gap-3">
          <span className="w-28 shrink-0 text-slate-600">Reason</span>
          <select
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={reason}
            onChange={(e) => setReason(e.target.value as ReplacementParcelReason)}
          >
            {SELECTABLE_REASONS.map((r) => (
              <option key={r} value={r}>
                {REASON_LABELS[r]}
              </option>
            ))}
          </select>
        </label>

        {itemsEditable && (
          <>
            <section className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Products sent</h3>
              {seededProducts.length === 0 && <p className="text-xs text-slate-400">No products in this parcel.</p>}
              {seededProducts.map((row, i) => (
                <ProductRowEditor
                  key={i}
                  products={products ?? []}
                  row={row}
                  onChange={(patch) =>
                    setProductRows(seededProducts.map((r, j) => (j === i ? { ...r, ...patch } : r)))
                  }
                  onRemove={() => setProductRows(seededProducts.filter((_, j) => j !== i))}
                />
              ))}
              <button
                type="button"
                disabled={!products?.length}
                onClick={() =>
                  setProductRows([
                    ...seededProducts,
                    { product_id: (products as Product[])[0].id, variant_id: null, qty: "1" },
                  ])
                }
                className="w-fit rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:text-slate-400"
              >
                + Add product
              </button>
            </section>

            <section className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Packaging used</h3>
              {seededMaterials.length === 0 && <p className="text-xs text-slate-400">No packaging recorded.</p>}
              {seededMaterials.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <MaterialSelect
                    materials={materials ?? []}
                    value={row.material_id}
                    kittingOnly
                    className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                    onChange={(materialId) =>
                      setMaterialRows(
                        seededMaterials.map((r, j) => (j === i ? { ...r, material_id: materialId } : r)),
                      )
                    }
                  />
                  <input
                    type="number"
                    min={0}
                    step="any"
                    aria-label="Packaging quantity"
                    className="w-24 rounded border border-slate-300 px-2 py-1 text-sm text-right tabular-nums"
                    value={row.qty}
                    onChange={(e) =>
                      setMaterialRows(seededMaterials.map((r, j) => (j === i ? { ...r, qty: e.target.value } : r)))
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setMaterialRows(seededMaterials.filter((_, j) => j !== i))}
                    className="text-xs text-red-600"
                  >
                    Remove
                  </button>
                </div>
              ))}
              <button
                type="button"
                disabled={!materials?.length}
                onClick={() => {
                  const used = new Set(seededMaterials.map((r) => r.material_id));
                  const next = (materials ?? []).find((m) => !used.has(m.id)) ?? materials?.[0];
                  if (next) setMaterialRows([...seededMaterials, { material_id: next.id, qty: "1" }]);
                }}
                className="w-fit rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:text-slate-400"
              >
                + Add packaging
              </button>
            </section>
          </>
        )}

        <section className="flex flex-col gap-2">
          <div className="flex items-center gap-3">
            <span className="w-28 shrink-0 text-slate-600">Postage</span>
            {completingPlaceholder ? (
              <span className="text-xs text-slate-400">The marketplace label this parcel was detected from.</span>
            ) : linkableCharges.length > 0 ? (
              <SegmentedControl<PostageMode>
                ariaLabel="Postage source"
                options={[
                  { value: "label", label: `${order.platform ? PLATFORM_LABELS[order.platform] : "Marketplace"} label` },
                  { value: "manual", label: "Enter amount" },
                ]}
                value={postageMode}
                onChange={setPostageMode}
              />
            ) : (
              <span className="text-xs text-slate-400">Entered by hand — no unclaimed marketplace label on this order.</span>
            )}
          </div>
          {postageMode === "label" && linkableCharges.length > 0 ? (
            <select
              aria-label="Marketplace label"
              className="ml-31 w-fit rounded border border-slate-300 px-2 py-1 text-sm"
              value={chargeId ?? ""}
              onChange={(e) => setChargeId(e.target.value ? Number(e.target.value) : null)}
            >
              {linkableCharges.map((c) => (
                <option key={c.id} value={c.id}>
                  Label #{c.sequence} · {formatMoney(c.amount, c.currency ?? order.currency)}
                  {c.posted_at ? ` · ${formatDayMonth(c.posted_at)}` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label="Postage cost"
              placeholder="0.00"
              className="ml-31 w-28 rounded border border-slate-300 px-2 py-1 text-sm"
              value={postageCost}
              onChange={(e) => setPostageCost(e.target.value)}
            />
          )}
        </section>

        <label className="flex items-center gap-3">
          <span className="w-28 shrink-0 text-slate-600">Tracking</span>
          <input
            className="w-56 rounded border border-slate-300 px-2 py-1 text-sm"
            value={tracking}
            onChange={(e) => setTracking(e.target.value)}
          />
          <input
            aria-label="Carrier"
            placeholder="Carrier"
            className="w-36 rounded border border-slate-300 px-2 py-1 text-sm"
            value={carrier}
            onChange={(e) => setCarrier(e.target.value)}
          />
        </label>
        <label className="flex items-start gap-3">
          <span className="mt-1 w-28 shrink-0 text-slate-600">Notes</span>
          <textarea
            rows={2}
            className="min-w-0 flex-1 resize-y rounded border border-slate-300 px-2 py-1 text-sm"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>

        <ErrorBanner error={mutation.error} />
      </div>
    </Modal>
  );
}

function ProductRowEditor({
  products,
  row,
  onChange,
  onRemove,
}: {
  products: Product[];
  row: ProductRow;
  onChange: (patch: Partial<ProductRow>) => void;
  onRemove: () => void;
}) {
  const { data: variants } = useQuery({
    queryKey: ["products", row.product_id, "variants"],
    queryFn: () => productsApi.listVariants(row.product_id),
  });
  const hasVariants = (variants?.length ?? 0) > 0;
  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="Product"
        className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
        value={row.product_id}
        onChange={(e) => onChange({ product_id: Number(e.target.value), variant_id: null })}
      >
        {products.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} {p.sku ? `(${p.sku})` : ""}
          </option>
        ))}
      </select>
      {hasVariants && (
        <select
          aria-label="Variant"
          className="w-40 rounded border border-slate-300 px-2 py-1 text-sm"
          value={row.variant_id ?? ""}
          onChange={(e) => onChange({ variant_id: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">Select variant…</option>
          {variants?.map((v) => (
            <option key={v.id} value={v.id}>
              {v.variant_name}
            </option>
          ))}
        </select>
      )}
      <input
        type="number"
        min={1}
        aria-label="Product quantity"
        className="w-20 rounded border border-slate-300 px-2 py-1 text-sm text-right tabular-nums"
        value={row.qty}
        onChange={(e) => onChange({ qty: e.target.value })}
      />
      <button type="button" onClick={onRemove} className="text-xs text-red-600">
        Remove
      </button>
    </div>
  );
}

function dedupe(rows: ProductRow[]): ProductRow[] {
  const seen = new Set<string>();
  return rows.filter((r) => {
    const key = `${r.product_id}:${r.variant_id ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
