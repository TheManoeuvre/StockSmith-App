import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { materialTypesApi } from "../../api/materialTypes";
import { ordersApi } from "../../api/orders";
import type { BoxCheck, BoxCheckConfirmInput, BoxCheckPreview } from "../../api/types";
import { formatMoney } from "../../lib/money";
import { ErrorBanner } from "../common/ErrorBanner";

interface Pick {
  on: boolean;
  qty: string;
}

// Which parcel box(es) an order used. Two jobs, one panel:
//
// Before shipping, an order whose items resolve to two or more different boxes (any material
// whose type is marked "Duplicates in Kitting Check") is blocked until someone picks the box(es)
// actually used. Nothing is pre-ticked on purpose: the point is that a person looks.
//
// After shipping, the same panel corrects an order that already went out with the wrong boxes
// recorded. That one moves stock (an unused box goes back on the shelf) and changes the order's
// packaging cost, so it shows a preview of exactly that before anything is saved.
export function OrderBoxCheckSection({ orderId, currency }: { orderId: number; currency: string | null }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["orders", orderId, "box-check"],
    queryFn: () => ordersApi.getBoxCheck(orderId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: materialTypes } = useQuery({ queryKey: ["material-types"], queryFn: materialTypesApi.list });

  const [picks, setPicks] = useState<Record<number, Pick>>({});
  const [extraIds, setExtraIds] = useState<number[]>([]);
  const [returnToStock, setReturnToStock] = useState(true);

  // Re-seed whenever the server's view changes (after a save, or moving to another order).
  useEffect(() => {
    // `boxes` guard: a partial/unexpected payload must never crash the whole order page.
    if (!data || !Array.isArray(data.boxes)) return;
    const next: Record<number, Pick> = {};
    for (const box of data.boxes) {
      const current = Number(box.qty);
      // Before shipping and not yet confirmed: start unticked so the person has to choose.
      const preselect = data.mode === "shipped" || !data.needs_check ? current > 0 : false;
      next[box.material_id] = { on: preselect, qty: current > 0 ? box.qty : "1" };
    }
    setPicks(next);
    setExtraIds([]);
    setReturnToStock(true);
  }, [data, orderId]);

  const flaggedTypeIds = useMemo(
    () => new Set((materialTypes ?? []).filter((t) => t.duplicates_in_kitting_check).map((t) => t.id)),
    [materialTypes],
  );
  const knownIds = new Set([...(data?.boxes ?? []).map((b) => b.material_id), ...extraIds]);
  const addable = (materials ?? []).filter(
    (m) => m.material_type_id != null && flaggedTypeIds.has(m.material_type_id) && !knownIds.has(m.id),
  );

  const selected = useMemo(
    () =>
      Object.entries(picks)
        .filter(([, p]) => p.on)
        .map(([id, p]) => ({ material_id: Number(id), qty: p.qty.trim() })),
    [picks],
  );
  const valid = selected.length > 0 && selected.every((b) => b.qty !== "" && Number(b.qty) > 0);
  const input: BoxCheckConfirmInput = { boxes: selected, return_to_stock: returnToStock };

  const { data: preview } = useQuery({
    queryKey: ["orders", orderId, "box-check-preview", JSON.stringify(input)],
    queryFn: () => ordersApi.previewBoxCheck(orderId, input),
    enabled: valid && data?.applies === true,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    queryClient.invalidateQueries({ queryKey: ["order-counts"] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };
  const confirmMutation = useMutation({
    mutationFn: () => ordersApi.confirmBoxCheck(orderId, input),
    onSuccess: invalidate,
  });
  const reviewedMutation = useMutation({
    mutationFn: () => ordersApi.markBoxCheckReviewed(orderId),
    onSuccess: invalidate,
  });

  if (!data || !data.applies) return null;

  const shipped = data.mode === "shipped";
  const rows = [
    ...data.boxes,
    ...extraIds
      .map((id) => materials?.find((m) => m.id === id))
      .filter((m): m is NonNullable<typeof m> => m != null)
      .map((m) => ({
        material_id: m.id,
        material_name: m.name,
        is_candidate: false,
        qty: "0",
        unit_cost: m.avg_unit_cost,
        free_stock: String(Number(m.current_qty) - Number(m.allocated_qty)),
      })),
  ];
  const setPick = (id: number, patch: Partial<Pick>) =>
    setPicks((prev) => {
      const base: Pick = prev[id] ?? { on: false, qty: "1" };
      return { ...prev, [id]: { ...base, ...patch } };
    });
  const addBox = (id: number) => {
    setExtraIds((prev) => [...prev, id]);
    setPick(id, { on: true, qty: "1" });
  };

  return (
    <div className="rounded bg-white p-4 shadow-sm" data-testid="box-check">
      <h2 className="mb-1 text-lg font-semibold">Parcel box</h2>
      <BoxCheckBanner data={data} />
      <p className="mb-2 text-sm text-slate-500">
        {shipped
          ? "Tick the box(es) this order actually shipped in. Anything you untick goes back into stock, and the order's packaging cost and net profit are recalculated."
          : "Tick the box(es) used for this order. You can approve more than one. Unticked boxes are released from reserved stock."}
      </p>
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Used</th>
            <th className="p-2">Box</th>
            <th className="p-2">Qty</th>
            <th className="p-2">{shipped ? "Recorded" : "Currently"}</th>
            <th className="p-2">Unit cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((box) => {
            const pick = picks[box.material_id] ?? { on: false, qty: "1" };
            return (
              <tr key={box.material_id} className="border-b border-slate-100">
                <td className="p-2">
                  <input
                    type="checkbox"
                    aria-label={`${box.material_name} used`}
                    checked={pick.on}
                    onChange={(e) => setPick(box.material_id, { on: e.target.checked })}
                  />
                </td>
                <td className="p-2">
                  {box.material_name}
                  {!box.is_candidate && <span className="ml-2 text-xs text-slate-400">Added by hand</span>}
                </td>
                <td className="p-2">
                  <input
                    aria-label={`${box.material_name} quantity`}
                    className="w-16 rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                    disabled={!pick.on}
                    value={pick.qty}
                    onChange={(e) => setPick(box.material_id, { qty: e.target.value })}
                  />
                </td>
                <td className="p-2 text-slate-500">{box.qty}</td>
                <td className="p-2 text-slate-500">{formatMoney(box.unit_cost, currency)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {addable.length > 0 && (
        <select
          aria-label="Add another box"
          className="mt-2 rounded border border-slate-300 px-2 py-1 text-sm"
          value=""
          onChange={(e) => e.target.value && addBox(Number(e.target.value))}
        >
          <option value="">+ Add another box…</option>
          {addable.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      )}

      {shipped && (
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={returnToStock} onChange={(e) => setReturnToStock(e.target.checked)} />
          Return removed boxes to stock
          <span className="text-xs text-slate-400">(untick if they were used up or damaged)</span>
        </label>
      )}

      {preview && valid && <BoxCheckPreviewPanel preview={preview} currency={currency} />}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => confirmMutation.mutate()}
          disabled={!valid || confirmMutation.isPending || (preview?.warnings.length ?? 0) > 0}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {shipped ? "Correct boxes" : "Confirm boxes"}
        </button>
        {shipped && data.needs_check && (
          <button
            onClick={() => reviewedMutation.mutate()}
            disabled={reviewedMutation.isPending}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Mark reviewed, no change
          </button>
        )}
      </div>
      <ErrorBanner error={confirmMutation.error ?? reviewedMutation.error} />
    </div>
  );
}

function BoxCheckBanner({ data }: { data: BoxCheck }) {
  if (data.mode === "pre_ship" && data.needs_check) {
    return (
      <p className="mb-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="alert">
        This order uses more than one parcel box. It can't be shipped until you confirm which was used.
      </p>
    );
  }
  if (data.mode === "shipped" && data.needs_check) {
    return (
      <p className="mb-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="alert">
        This order shipped with more than one parcel box recorded. Check it, correct it if needed, or mark it reviewed.
      </p>
    );
  }
  if (data.confirmed_at) {
    return (
      <p className="mb-2 text-xs text-slate-500">
        Boxes confirmed {new Date(data.confirmed_at).toLocaleString()}.
      </p>
    );
  }
  return null;
}

function BoxCheckPreviewPanel({
  preview,
  currency,
}: {
  preview: BoxCheckPreview;
  currency: string | null;
}) {
  const delta = Number(preview.net_profit_delta);
  return (
    <div className="mt-3 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
      {preview.changes.length === 0 ? (
        <p className="text-slate-500">No change from what's recorded.</p>
      ) : (
        <ul className="mb-1 list-disc pl-5">
          {preview.changes.map((c) => {
            const effect = Number(c.stock_effect);
            return (
              <li key={c.material_id}>
                {c.material_name}: {c.current_qty} → {c.new_qty}
                {effect > 0 && ` · ${effect} returned to stock`}
                {effect < 0 && ` · ${-effect} taken from stock`}
              </li>
            );
          })}
        </ul>
      )}
      <p>
        Packaging cost {formatMoney(preview.kitting_cost_before, currency)} →{" "}
        {formatMoney(preview.kitting_cost_after, currency)}
        {delta !== 0 && (
          <span className={delta > 0 ? "text-green-700" : "text-red-700"}>
            {" "}
            · net profit {delta > 0 ? "+" : "−"}
            {formatMoney(String(Math.abs(delta)), currency)}
          </span>
        )}
      </p>
      {preview.warnings.map((w) => (
        <p key={w} className="text-red-700" role="alert">
          {w}
        </p>
      ))}
    </div>
  );
}
