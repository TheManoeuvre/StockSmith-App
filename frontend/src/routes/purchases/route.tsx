import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { useMemo, useState, type MouseEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { materialsApi } from "../../api/materials";
import { purchasesApi } from "../../api/purchases";
import type { Material, Purchase, PurchaseStatus } from "../../api/types";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { FilterTabs } from "../../components/common/FilterTabs";
import { Th } from "../../components/common/ListTable";
import { PurchaseStatusPill } from "../../components/purchases/PurchaseStatusPill";
import { formatMoney } from "../../lib/money";
import { formatDayMonth, qtyWithUnit } from "../../lib/format";

/**
 * Pathless layout for /purchases: the list lives here (not in index.tsx) so it stays mounted
 * and visible while `$purchaseId`/`new` render into the `<Outlet>` as a slide-over panel on
 * top of it — see components/common/DetailPanel.tsx.
 */
export const Route = createFileRoute("/purchases")({
  component: PurchasesLayout,
});

const STATUS_TABS: { id: PurchaseStatus | "all"; label: string }[] = [
  { id: "all", label: "All purchases" },
  { id: "ordered", label: "Ordered" },
  { id: "partially_received", label: "Part received" },
  { id: "received", label: "Received" },
];

function lineTotal(purchase: Purchase): number {
  return purchase.lines.reduce((sum, l) => sum + Number(l.total_cost), 0);
}

function outstandingLineCount(purchase: Purchase): number {
  return purchase.lines.filter((l) => Number(l.outstanding_qty) > 0).length;
}

function isLate(purchase: Purchase): boolean {
  if (purchase.status === "received" || !purchase.expected_arrival_date) return false;
  return purchase.expected_arrival_date < new Date().toISOString().slice(0, 10);
}

function PurchasesLayout() {
  return (
    <>
      <PurchasesListContent />
      <Outlet />
    </>
  );
}

function PurchasesListContent() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<PurchaseStatus | "all">("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["purchases"],
    queryFn: () => purchasesApi.list(),
  });
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
  });
  const materialById = useMemo(
    () => new Map((materials ?? []).map((m) => [m.id, m])),
    [materials],
  );

  const receiveMutation = useMutation({
    mutationFn: (id: number) => purchasesApi.receive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
    },
  });

  const purchases = data ?? [];
  const countFor = (id: PurchaseStatus | "all") =>
    id === "all" ? purchases.length : purchases.filter((p) => p.status === id).length;
  const rows = tab === "all" ? purchases : purchases.filter((p) => p.status === tab);

  const openN = purchases.filter((p) => p.status !== "received").length;
  const partN = purchases.filter((p) => p.status === "partially_received").length;
  const onOrderTotal = purchases
    .filter((p) => p.status !== "received")
    .reduce((sum, p) => sum + lineTotal(p), 0);

  if (isLoading) return <p>Loading purchases…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Purchases</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {openN} awaiting delivery · {partN} part received ·{" "}
            {formatMoney(String(onOrderTotal), "GBP")} on order
          </p>
        </div>
        <Link
          to="/purchases/new"
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
        >
          New purchase
        </Link>
      </div>

      <ErrorBanner error={receiveMutation.error} />

      <FilterTabs
        tabs={STATUS_TABS.map((t) => ({
          id: t.id,
          label: t.label,
          count: countFor(t.id),
        }))}
        active={tab}
        onChange={(id) => setTab(id as PurchaseStatus | "all")}
      />

      <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>Order</Th>
            <Th>Supplier</Th>
            <Th>Due</Th>
            <Th>Lines · ordered / in</Th>
            <Th>Status</Th>
            <Th align="right">Total</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((purchase) => (
            <PurchaseRow
              key={purchase.id}
              purchase={purchase}
              materialById={materialById}
              onOpen={(receiving) =>
                navigate({
                  to: "/purchases/$purchaseId",
                  params: { purchaseId: String(purchase.id) },
                  search: receiving ? { tab: "receiving" } : {},
                })
              }
              onReceiveAll={() => receiveMutation.mutate(purchase.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PurchaseRow({
  purchase,
  materialById,
  onOpen,
  onReceiveAll,
}: {
  purchase: Purchase;
  materialById: Map<number, Material>;
  onOpen: (receiving?: boolean) => void;
  onReceiveAll: () => void;
}) {
  const openDetail = (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest("a, button, input, select, label")) return;
    onOpen();
  };
  const out = outstandingLineCount(purchase);
  const late = isLate(purchase);

  return (
    <tr
      onClick={openDetail}
      className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50"
    >
      <td className="p-2 align-top">
        <div className="font-medium">#{purchase.id}</div>
        <div className="text-[11px] text-slate-400">
          placed {formatDayMonth(purchase.order_date)}
        </div>
      </td>
      <td className="p-2 align-top">{purchase.supplier_name ?? "—"}</td>
      <td className="p-2 align-top">
        <div className={late ? "text-red-600" : "text-slate-500"}>
          {purchase.expected_arrival_date
            ? formatDayMonth(purchase.expected_arrival_date)
            : "—"}
        </div>
        {late && <div className="text-[11px] font-semibold text-red-600">late</div>}
      </td>
      <td className="p-2 align-top">
        {purchase.lines.map((l) => {
          const material = materialById.get(l.material_id);
          const name = material?.name ?? `#${l.material_id}`;
          const full = Number(l.received_qty) >= Number(l.qty);
          const some = Number(l.received_qty) > 0;
          return (
            <div key={l.id} className="flex items-center gap-2 leading-tight">
              <span className="min-w-0 flex-1 truncate">{name}</span>
              <span className="tabular-nums text-slate-400">
                {qtyWithUnit(l.qty, material?.unit)}
              </span>
              <span
                className={`w-16 whitespace-nowrap text-right font-semibold tabular-nums ${
                  full ? "text-emerald-700" : some ? "text-sky-700" : "text-slate-400"
                }`}
              >
                {qtyWithUnit(l.received_qty, material?.unit)} in
              </span>
            </div>
          );
        })}
        <div className="mt-0.5 text-[11px] text-slate-400">
          {out > 0 ? `${out} line${out === 1 ? "" : "s"} outstanding` : "complete"}
        </div>
      </td>
      <td className="p-2 align-top">
        <PurchaseStatusPill status={purchase.status} />
      </td>
      <td className="p-2 text-right align-top tabular-nums">
        {formatMoney(String(lineTotal(purchase)), "GBP")}
      </td>
      <td className="p-2 text-right align-top">
        {purchase.status === "ordered" ? (
          <button
            onClick={onReceiveAll}
            className="rounded border border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold hover:bg-slate-50"
          >
            Receive all
          </button>
        ) : purchase.status === "partially_received" ? (
          <button
            onClick={() => onOpen()}
            className="rounded border border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold hover:bg-slate-50"
          >
            Receive rest
          </button>
        ) : (
          <button
            onClick={() => onOpen(true)}
            className="rounded border border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold hover:bg-slate-50"
          >
            Deliveries
          </button>
        )}
      </td>
    </tr>
  );
}
