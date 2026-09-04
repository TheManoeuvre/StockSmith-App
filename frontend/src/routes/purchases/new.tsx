import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { purchasesApi, type PurchaseLineInput } from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { NewPurchaseLineEditor } from "../../components/purchases/NewPurchaseLineEditor";
import { PurchaseStockAlerts } from "../../components/purchases/PurchaseStockAlerts";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { FieldRow } from "../../components/common/FieldRow";
import { Stat } from "../../components/common/Stat";
import { formatMoney } from "../../lib/money";
import { displayQty, formatDayMonth, isLowStock } from "../../lib/format";
import { coverOnArrival } from "../../lib/reorder";

export const Route = createFileRoute("/purchases/new")({
  component: NewPurchase,
});

const money = (n: number) => formatMoney(String(n), "GBP");

function NewPurchase() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
  });
  const { data: suppliers } = useQuery({
    queryKey: ["suppliers"],
    queryFn: suppliersApi.list,
  });

  const [supplier, setSupplier] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [supplierOrderNumber, setSupplierOrderNumber] = useState("");
  const [orderDate, setOrderDate] = useState("");
  const [expectedArrivalDate, setExpectedArrivalDate] = useState("");
  const [notes, setNotes] = useState("");
  const [deliveryCost, setDeliveryCost] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([]);

  // Materials that would show in the Stock alerts card — their prices are worth pulling
  // alongside the lines' so the "at last price" figure there is the real one.
  const lowMaterialIds = useMemo(
    () =>
      (materials ?? [])
        .filter(
          (m) =>
            Number(m.reorder_threshold) > 0 &&
            (m.stockout_status === "critical" ||
              m.stockout_status === "warning" ||
              isLowStock(m.current_qty, m.reorder_threshold)),
        )
        .map((m) => m.id),
    [materials],
  );
  const refMaterialIds = useMemo(() => {
    const ids = new Set<number>([
      ...lines.map((l) => l.material_id),
      ...lowMaterialIds,
    ]);
    return [...ids].sort((a, b) => a - b);
  }, [lines, lowMaterialIds]);

  const { data: priceRefs } = useQuery({
    queryKey: ["purchase-price-reference", supplierId, refMaterialIds],
    queryFn: () => purchasesApi.priceReference(supplierId, refMaterialIds),
    enabled: refMaterialIds.length > 0,
  });
  const priceByMaterial = useMemo(
    () => new Map((priceRefs ?? []).map((p) => [p.material_id, p])),
    [priceRefs],
  );

  const materialById = useMemo(
    () => new Map((materials ?? []).map((m) => [m.id, m])),
    [materials],
  );

  const goodsTotal = lines.reduce((s, l) => s + (Number(l.total_cost) || 0), 0);
  const deliveryNum = Number(deliveryCost) || 0;
  const orderTotal = goodsTotal + deliveryNum;
  const totalUnits = lines.reduce((s, l) => s + (Number(l.qty) || 0), 0);
  const completeLines = lines.filter(
    (l) => l.material_id && Number(l.qty) > 0 && Number(l.total_cost) > 0,
  );

  const linesAbovePrice = lines.filter((l) => {
    const ref = priceByMaterial.get(l.material_id);
    const lastUnit = ref ? Number(ref.unit_cost) : null;
    const qty = Number(l.qty) || 0;
    const unitPrice = qty > 0 ? (Number(l.total_cost) || 0) / qty : 0;
    return lastUnit != null && lastUnit > 0 && unitPrice > lastUnit * 1.005;
  }).length;

  const coverStat = useMemo(() => {
    const covers = lines
      .map((l) => {
        const m = materialById.get(l.material_id);
        return m
          ? coverOnArrival(m, expectedArrivalDate || null, Number(l.qty) || 0, m.lead_time_days)
          : null;
      })
      .filter((c): c is number => c != null);
    const measuredAt = expectedArrivalDate
      ? `measured at ${formatDayMonth(expectedArrivalDate)}`
      : "measured on arrival";
    if (covers.length === 0) return { value: "—", sub: measuredAt };
    const thin = covers.filter((c) => c < 4).length;
    return {
      value: thin ? "Thin" : "Healthy",
      sub: thin ? `${thin} line${thin === 1 ? "" : "s"} under 4 wk` : measuredAt,
    };
  }, [lines, materialById, expectedArrivalDate]);

  const blockReason =
    lines.length === 0
      ? "Add at least one order line"
      : completeLines.length === 0
        ? "Every line needs a quantity and a line total"
        : null;

  const createMutation = useMutation({
    mutationFn: async () => {
      let resolvedSupplierId = supplierId;
      if (!resolvedSupplierId && supplier.trim()) {
        resolvedSupplierId = (await suppliersApi.findOrCreate(supplier.trim())).id;
      }
      return purchasesApi.create({
        supplier_id: resolvedSupplierId,
        supplier_order_number: supplierOrderNumber.trim() || null,
        order_date: orderDate || null,
        expected_arrival_date: expectedArrivalDate || null,
        notes: notes || null,
        delivery_cost: deliveryCost.trim() || null,
        lines,
      });
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      navigate({
        to: "/purchases/$purchaseId",
        params: { purchaseId: String(created.id) },
      });
    },
  });

  const orderTotalSub =
    linesAbovePrice > 0
      ? `${linesAbovePrice} line${linesAbovePrice === 1 ? "" : "s"} above last price`
      : deliveryNum > 0
        ? `incl ${money(deliveryNum)} delivery`
        : "no delivery charge";

  return (
    <DetailPanel
      title="New purchase"
      onClose={() => navigate({ to: "/purchases" })}
      footer={
        <div className="flex items-center justify-between gap-3">
          <span className="text-[12px] text-slate-500">
            {blockReason ?? "Ready to raise purchase order"}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => navigate({ to: "/purchases" })}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
            >
              Discard
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!!blockReason || createMutation.isPending}
              title={blockReason ?? undefined}
              className="rounded bg-slate-900 px-4 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Raise purchase order
            </button>
          </div>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="h-14 w-14 shrink-0 rounded border border-slate-200 bg-slate-50" />
            <div className="min-w-0">
              <p className="text-[11px] uppercase tracking-wide text-slate-400">
                New purchase
              </p>
              <p className="truncate text-sm font-medium text-slate-700">
                {supplier || "Choose a supplier"}
              </p>
              <p className="mt-0.5 flex items-center gap-1.5 text-[11.5px]">
                <span
                  className={`inline-block h-1.5 w-1.5 rounded-full ${
                    blockReason ? "bg-amber-500" : "bg-emerald-500"
                  }`}
                />
                <span className={blockReason ? "text-amber-700" : "text-emerald-700"}>
                  {blockReason ?? "Ready to raise"}
                </span>
                <span className="text-slate-300">·</span>
                <span className="text-slate-500">
                  {lines.length} line{lines.length === 1 ? "" : "s"} · {money(orderTotal)}
                </span>
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Stat
              label="Lines"
              value={String(lines.length)}
              sub={`${displayQty(totalUnits)} units`}
            />
            <Stat
              label="Order total"
              value={money(orderTotal)}
              valueClassName={linesAbovePrice > 0 ? "text-amber-600" : undefined}
              sub={orderTotalSub}
            />
            <Stat
              label="Cover on arrival"
              value={coverStat.value}
              sub={coverStat.sub}
              tone="highlight"
            />
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm">
          <FieldRow label="Supplier">
            <CreatableSelect
              className="w-full rounded border border-slate-300 px-2 py-1"
              options={suppliers ?? []}
              value={supplier}
              onChange={setSupplier}
              onResolved={setSupplierId}
            />
          </FieldRow>
          <FieldRow label="Order date">
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={orderDate}
              onChange={(e) => setOrderDate(e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Expected delivery">
            <div className="flex flex-col gap-0.5">
              <input
                type="date"
                className="rounded border border-slate-300 px-2 py-1"
                value={expectedArrivalDate}
                onChange={(e) => setExpectedArrivalDate(e.target.value)}
              />
              <span className="text-[11px] text-slate-400">
                cover below is measured at this date
              </span>
            </div>
          </FieldRow>
          <FieldRow label="Supplier order #">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={supplierOrderNumber}
              onChange={(e) => setSupplierOrderNumber(e.target.value)}
              placeholder="Their PO / order number"
            />
          </FieldRow>
          <FieldRow label="Internal note">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </FieldRow>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-baseline gap-2">
            <h2 className="text-[13px] font-semibold">Order lines</h2>
            <span className="text-[12.5px] text-slate-400">
              quantity · line total as invoiced
            </span>
            <span className="ml-auto text-[12.5px] font-semibold tabular-nums">
              {money(goodsTotal)}
            </span>
          </div>

          <NewPurchaseLineEditor
            materials={materials ?? []}
            lines={lines}
            onChange={setLines}
            priceByMaterial={priceByMaterial}
            expectedArrivalDate={expectedArrivalDate}
          />

          <div className="flex items-center gap-2 rounded border border-slate-200 bg-slate-50/60 px-3 py-2 text-[12px]">
            <span className="text-slate-600">Delivery &amp; carriage</span>
            <span className="text-[11px] text-slate-400">
              charged on the order, not allocated to unit costs
            </span>
            <span className="ml-auto text-[11px] text-slate-400">£</span>
            <input
              type="number"
              step="0.5"
              className="w-24 rounded border border-slate-300 px-2 py-1 text-right text-sm tabular-nums"
              value={deliveryCost}
              onChange={(e) => setDeliveryCost(e.target.value)}
              aria-label="Delivery and carriage"
            />
          </div>

          <div className="flex items-center gap-2 px-3 py-1 text-[13px]">
            <span className="font-semibold">Order total</span>
            <span className="ml-auto font-bold tabular-nums">{money(orderTotal)}</span>
          </div>
        </div>

        <PurchaseStockAlerts
          materials={materials ?? []}
          supplierId={supplierId}
          supplierName={supplier}
          lines={lines}
          priceByMaterial={priceByMaterial}
          onAdd={(materialId, qty, totalCost) =>
            setLines((prev) => [
              ...prev,
              { material_id: materialId, qty, total_cost: totalCost },
            ])
          }
        />

        <ErrorBanner error={createMutation.error} />
      </div>
    </DetailPanel>
  );
}
