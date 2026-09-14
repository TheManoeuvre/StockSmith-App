import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { shippingPriceForChannel } from "../../lib/shippingPrice";
import { ordersApi } from "../../api/orders";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import type { Order } from "../../api/types";
import { CopyButton } from "../common/CopyButton";
import { ErrorBanner } from "../common/ErrorBanner";
import { FieldRow } from "../common/FieldRow";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useManagedSave } from "../../hooks/useDirtyRegistry";
import { formatMoney } from "../../lib/money";
import { formatDayMonth } from "../../lib/format";

interface ShippingEdits {
  profileId: string;
  charged: string;
}

/**
 * The Shipping tab. Profile + postage-charged are editable only on a manual order that
 * hasn't shipped (marketplace orders get their shipping from sync, and a shipped order's
 * figures are frozen) — otherwise they show read-only. Tracking number/carrier come from
 * marketplace sync (read-only, never set for manual orders). Ship-to / postage-actually-paid
 * are in the design but have no backend column yet, so they're still deferred.
 */
export function OrderShippingForm({ order, onSaved }: { order: Order; onSaved: () => void }) {
  const editable = order.platform === null && order.status !== "shipped";

  const { data: shippingProfiles } = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    queryFn: () => shippingProfilesApi.list(),
    enabled: editable,
  });
  const profiles = shippingProfiles ?? [];

  const seed = useMemo<ShippingEdits>(
    () => ({
      profileId: order.shipping_profile_id != null ? String(order.shipping_profile_id) : "",
      charged: order.shipping_charged ?? "",
    }),
    [order.shipping_profile_id, order.shipping_charged],
  );
  const { value, setValue, isDirty, markSaved, revert } = useEditableCopy<ShippingEdits>({
    key: "order-shipping",
    label: "Shipping",
    initial: { profileId: "", charged: "" },
    seed,
    seedKey: order.id,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      ordersApi.update(order.id, {
        shipping_profile_id: value.profileId ? Number(value.profileId) : null,
        shipping_charged: value.charged || null,
      }),
    onSuccess: () => {
      markSaved();
      onSaved();
    },
  });
  const managed = useManagedSave("order-shipping", {
    save: () => saveMutation.mutate(),
    revert,
  });

  return (
    <div className="flex flex-col gap-3 rounded bg-white p-4 text-sm shadow-sm">
      <h2 className="text-sm font-medium text-slate-600">Shipping</h2>

      {editable ? (
        <>
          <FieldRow label="Shipping profile" align="right">
            <select
              className="w-48 rounded border border-slate-300 px-2 py-1"
              value={value.profileId}
              onChange={(e) => {
                const id = e.target.value;
                const profile = profiles.find((p) => String(p.id) === id);
                setValue((prev) => ({
                  profileId: id,
                  charged: profile ? shippingPriceForChannel(profile, order.manual_channel) : prev.charged,
                }));
              }}
            >
              <option value="">No profile</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </FieldRow>
          <FieldRow label="Postage charged" align="right">
            <input
              className="w-28 rounded border border-slate-300 px-2 py-1 text-right tabular-nums"
              placeholder="0.00"
              value={value.charged}
              onChange={(e) => setValue((prev) => ({ ...prev, charged: e.target.value }))}
            />
          </FieldRow>
          {!managed && (
            <div>
              <button
                onClick={() => saveMutation.mutate()}
                disabled={!isDirty || saveMutation.isPending}
                className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50"
              >
                Save
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <FieldRow label="Shipping profile" align="right">
            <span className="text-slate-600">{order.shipping_profile_name ?? "—"}</span>
          </FieldRow>
          <FieldRow label="Postage charged" align="right">
            <span className="tabular-nums text-slate-600">
              {formatMoney(order.shipping_charged, order.currency)}
            </span>
          </FieldRow>
        </>
      )}

      {order.tracking_number && (
        <FieldRow label="Tracking number" align="right">
          <span className="flex items-center justify-end gap-1 tabular-nums text-slate-600">
            {order.carrier ? `${order.carrier} · ${order.tracking_number}` : order.tracking_number}
            <CopyButton value={order.tracking_number} label="Copy tracking number" />
          </span>
        </FieldRow>
      )}
      <FieldRow label="Shipped on" align="right">
        <span className="text-slate-600">
          {order.shipped_at ? formatDayMonth(order.shipped_at) : "Not yet shipped"}
        </span>
      </FieldRow>
      <FieldRow label="Postage cost" align="right">
        <span
          className={`tabular-nums ${order.postage_cost_missing ? "text-amber-700" : "text-slate-600"}`}
        >
          {order.shipping_cost_snapshot != null
            ? `−${formatMoney(order.shipping_cost_snapshot, order.currency)}`
            : order.postage_cost_missing
              ? "Not recorded"
              : "—"}
        </span>
      </FieldRow>

      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
