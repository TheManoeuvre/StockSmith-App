import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { stockTakesApi } from "../../api/stockTakes";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { roundQty } from "../../lib/format";

export const Route = createFileRoute("/stock-takes/unresolved")({
  component: UnresolvedVariances,
});

/**
 * Flagged lines from takes that have already closed.
 *
 * Its own page rather than a filter on each take, because the point is that following one
 * up shouldn't depend on remembering which take it came from. A take can close with these
 * outstanding by design — they carry forward rather than holding it open.
 */
function UnresolvedVariances() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["unresolved-variances"],
    queryFn: stockTakesApi.unresolvedVariances,
  });

  const resolveMutation = useMutation({
    mutationFn: ({
      takeId,
      lineId,
      action,
    }: {
      takeId: number;
      lineId: number;
      action: "accept_counted" | "accept_system" | "reset";
    }) => stockTakesApi.resolveLine(takeId, lineId, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["unresolved-variances"] });
      queryClient.invalidateQueries({ queryKey: ["stock-takes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Unresolved variances</h1>
        <Link to="/stock-takes" className="rounded border border-slate-300 px-3 py-2 text-sm">
          Back to stock takes
        </Link>
      </div>

      {data && data.length === 0 ? (
        <p className="text-slate-500">Nothing outstanding — every counted difference has been settled.</p>
      ) : (
        <>
          <p className="text-sm text-slate-500">
            Counted differences that couldn't be applied automatically, from takes that have since closed. Each one is
            two different truths rather than a miscount, so it's your call which is right.
          </p>
          <div className="flex flex-col gap-2">
            {data?.map(({ line, stock_take_id, stock_take_closed_at }) => (
              <div key={line.id} className="rounded border border-amber-300 bg-amber-50 p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-medium">{line.name}</p>
                  <p className="text-xs text-slate-500">
                    From{" "}
                    <Link
                      to="/stock-takes/$stockTakeId"
                      params={{ stockTakeId: String(stock_take_id) }}
                      className="underline"
                    >
                      stock take #{stock_take_id}
                    </Link>
                    {stock_take_closed_at && <>, closed {new Date(stock_take_closed_at).toLocaleDateString()}</>}
                  </p>
                </div>
                <p className="text-sm text-amber-800">{line.conflict_reason}</p>
                <p className="mt-1 text-sm">
                  Expected {roundQty(line.expected_qty)} · counted{" "}
                  {line.counted_qty === null ? "—" : roundQty(line.counted_qty)}
                  {line.system_qty_at_approval !== null && <> · system now {roundQty(line.system_qty_at_approval)}</>}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    onClick={() =>
                      resolveMutation.mutate({ takeId: stock_take_id, lineId: line.id, action: "accept_counted" })
                    }
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Use the counted figure
                  </button>
                  <button
                    onClick={() =>
                      resolveMutation.mutate({ takeId: stock_take_id, lineId: line.id, action: "accept_system" })
                    }
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Keep what the system says
                  </button>
                  <button
                    onClick={() => resolveMutation.mutate({ takeId: stock_take_id, lineId: line.id, action: "reset" })}
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Clear and count again later
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      <ErrorBanner error={resolveMutation.error} />
    </div>
  );
}
