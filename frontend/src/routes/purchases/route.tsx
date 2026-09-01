import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { purchasesApi } from "../../api/purchases";
import type { Purchase, PurchaseStatus } from "../../api/types";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { GroupHeaderRow, Th } from "../../components/common/ListTable";
import { PurchaseStatusPill } from "../../components/purchases/PurchaseStatusPill";

/**
 * Pathless layout for /purchases: the list lives here (not in index.tsx) so it stays mounted
 * and visible while `$purchaseId`/`new` render into the `<Outlet>` as a slide-over panel on
 * top of it — see components/common/DetailPanel.tsx. index.tsx is a trivial route now; this
 * component is what actually renders the list.
 */
export const Route = createFileRoute("/purchases")({
  component: PurchasesLayout,
});

// Outstanding work first, so the list opens on what there's something to do about; "Received"
// last since those are done. Not paginated (the whole list loads at once), so grouping here
// doesn't fight pagination boundaries the way it would on Orders/Products.
const STATUS_ORDER: PurchaseStatus[] = [
  "ordered",
  "partially_received",
  "received",
];
const STATUS_GROUP_LABELS: Record<PurchaseStatus, string> = {
  ordered: "Ordered",
  partially_received: "Part received",
  received: "Received",
};

function groupByStatus(
  purchases: Purchase[],
): { status: PurchaseStatus; purchases: Purchase[] }[] {
  return STATUS_ORDER.map((status) => ({
    status,
    purchases: purchases.filter((p) => p.status === status),
  })).filter((g) => g.purchases.length > 0);
}

function lineTotal(purchase: Purchase): number {
  return purchase.lines.reduce((sum, l) => sum + Number(l.total_cost), 0);
}

/** How much of the order has actually turned up, counted in lines rather than units —
 *  "7 of 10 lines" is what someone chasing a supplier wants to see at a glance. */
function progress(purchase: Purchase): string {
  const settled = purchase.lines.filter(
    (l) => Number(l.outstanding_qty) === 0,
  ).length;
  return `${settled} of ${purchase.lines.length}`;
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
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["purchases"],
    queryFn: () => purchasesApi.list(),
  });

  const receiveMutation = useMutation({
    mutationFn: (id: number) => purchasesApi.receive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
    },
  });

  const unreceiveMutation = useMutation({
    mutationFn: (id: number) => purchasesApi.unreceive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
    },
  });

  const groups = useMemo(() => groupByStatus(data ?? []), [data]);

  if (isLoading) return <p>Loading purchases…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Purchases</h1>
        <Link
          to="/purchases/new"
          className="rounded bg-slate-900 px-4 py-2 text-white"
        >
          New purchase
        </Link>
      </div>

      <ErrorBanner error={receiveMutation.error ?? unreceiveMutation.error} />

      <table className="w-full border-collapse bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <Th>Supplier</Th>
            <Th>Order date</Th>
            <Th>Status</Th>
            <Th>Lines in</Th>
            <Th>Total cost</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        {groups.map((group) => (
          <tbody key={group.status}>
            <GroupHeaderRow
              label={STATUS_GROUP_LABELS[group.status]}
              count={group.purchases.length}
              colSpan={6}
            />
            {group.purchases.map((purchase) => (
              <tr
                key={purchase.id}
                className="border-b border-slate-100 hover:bg-slate-50"
              >
                <td className="p-2">
                  <Link
                    to="/purchases/$purchaseId"
                    params={{ purchaseId: String(purchase.id) }}
                    className="text-slate-900 underline"
                  >
                    {purchase.supplier_name ?? "—"}
                  </Link>
                </td>
                <td className="p-2">{purchase.order_date}</td>
                <td className="p-2">
                  <PurchaseStatusPill status={purchase.status} />
                </td>
                <td className="p-2">{progress(purchase)}</td>
                <td className="p-2">£{lineTotal(purchase).toFixed(2)}</td>
                <td className="p-2">
                  {/* One click only for the case it can be one click: nothing has arrived, so
                    "receive" can only mean all of it. A part-received order needs per-line
                    figures, which is the detail page's job. */}
                  {purchase.status === "ordered" ? (
                    <button
                      onClick={() => receiveMutation.mutate(purchase.id)}
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                    >
                      Receive all
                    </button>
                  ) : purchase.status === "partially_received" ? (
                    <Link
                      to="/purchases/$purchaseId"
                      params={{ purchaseId: String(purchase.id) }}
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                    >
                      Receive rest
                    </Link>
                  ) : (
                    <button
                      onClick={() => unreceiveMutation.mutate(purchase.id)}
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                    >
                      Un-receive
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}
