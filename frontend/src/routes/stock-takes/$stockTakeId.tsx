import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useMemo, useState } from "react";
import { stockTakesApi } from "../../api/stockTakes";
import type { StockTakeDetail, StockTakeLine } from "../../api/types";
import { ConfirmDialog } from "../../components/common/ConfirmDialog";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { SaveButton } from "../../components/common/SaveButton";
import { StockTakeCsvPanel } from "../../components/stockTakes/StockTakeCsvPanel";
import {
  countedInGroup,
  groupLabel,
  groupLines,
} from "../../components/stockTakes/groupLines";
import type { TabDef } from "../../components/common/Tabs";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useSiblingNav } from "../../hooks/useSiblingNav";
import { roundQty } from "../../lib/format";

const TAB_IDS = ["count", "review"] as const;
type TabId = (typeof TAB_IDS)[number];

export const Route = createFileRoute("/stock-takes/$stockTakeId")({
  component: StockTakeDetailPage,
  // Same reasoning as the product page: keeping the tab in the URL makes switching one a
  // real router navigation, so the root unsaved-changes blocker covers leaving a
  // half-entered count sheet without this page knowing the guard exists.
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    const tab = search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

const TABS: TabDef[] = [
  { id: "count", label: "Count sheet" },
  { id: "review", label: "Review" },
];

/** Counts as typed, keyed by line id. Strings because they're bound to text inputs — "" is
 * a cleared count, which is a different thing from "0". */
type CountForm = Record<number, string>;

function toForm(take: StockTakeDetail | undefined): CountForm | undefined {
  if (!take) return undefined;
  return Object.fromEntries(
    take.lines.map((l) => [
      l.id,
      l.counted_qty === null ? "" : roundQty(l.counted_qty),
    ]),
  );
}

function StockTakeDetailPage() {
  const { stockTakeId } = Route.useParams();
  const id = Number(stockTakeId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const activeTab = Route.useSearch().tab ?? "count";

  const { data: take } = useQuery({
    queryKey: ["stock-takes", id],
    queryFn: () => stockTakesApi.get(id),
  });
  const { prevId, nextId } = useSiblingNav(
    ["stock-takes"],
    id,
    (data) => data as { id: number }[] | undefined,
  );
  const closePanel = useCallback(
    () => navigate({ to: "/stock-takes" }),
    [navigate],
  );
  const goPrev = useCallback(
    () =>
      navigate({
        to: "/stock-takes/$stockTakeId",
        params: { stockTakeId: String(prevId) },
      }),
    [navigate, prevId],
  );
  const goNext = useCallback(
    () =>
      navigate({
        to: "/stock-takes/$stockTakeId",
        params: { stockTakeId: String(nextId) },
      }),
    [navigate, nextId],
  );
  const [confirmingApprove, setConfirmingApprove] = useState(false);
  // Collapsed groups, by key. A two-hundred-line sheet is unusable as one list, and the
  // point of grouping it is to be able to work through one shelf at a time.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const seed = useMemo(() => toForm(take), [take]);
  // The order is the server's — it arrives arranged, and re-sorting here would be a second
  // opinion that could disagree with the CSV someone printed. This only finds the headings.
  const groups = useMemo(() => groupLines(take?.lines ?? []), [take?.lines]);
  const {
    value: counts,
    setValue: setCounts,
    isDirty,
    markSaved,
  } = useEditableCopy<CountForm>({
    key: "count-sheet",
    label: "Stock take counts",
    initial: {},
    seed,
    seedKey: id,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["stock-takes", id] });
    queryClient.invalidateQueries({ queryKey: ["stock-takes"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      stockTakesApi.setLineCounts(
        id,
        (take?.lines ?? []).map((l) => ({
          line_id: l.id,
          // "" clears the count back to "not counted" rather than storing a zero.
          counted_qty:
            (counts[l.id] ?? "").trim() === "" ? null : counts[l.id].trim(),
          notes: l.notes,
        })),
      ),
    onSuccess: (updated) => {
      markSaved(toForm(updated));
      invalidate();
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  const approveMutation = useMutation({
    mutationFn: () => stockTakesApi.approve(id),
    onSuccess: () => {
      setConfirmingApprove(false);
      invalidate();
      navigate({
        to: "/stock-takes/$stockTakeId",
        search: { tab: "review" },
        params: { stockTakeId },
      });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({
      lineId,
      action,
    }: {
      lineId: number;
      action: "accept_counted" | "accept_system" | "reset";
    }) => stockTakesApi.resolveLine(id, lineId, action),
    onSuccess: () => invalidate(),
  });

  if (!take) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  const isOpen = take.status === "open";
  const pendingApproval = take.lines.filter(
    (l) => l.status === "counted",
  ).length;
  const blanks = take.lines.filter(
    (l) => l.counted_qty === null && l.status === "pending",
  ).length;

  return (
    <DetailPanel
      title={`Stock take #${take.id}`}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      tabs={TABS}
      activeTab={activeTab}
      onTabChange={(tab) =>
        navigate({
          to: "/stock-takes/$stockTakeId",
          search: { tab: tab as TabId },
          params: { stockTakeId },
        })
      }
    >
      <div className="flex flex-col gap-4">
        <div>
          <p className="text-sm text-slate-500">
            {take.scope_description}. Started{" "}
            {new Date(take.started_at).toLocaleDateString()}
            {isOpen ? (
              <>
                {" "}
                —{" "}
                <span className="text-amber-800">
                  open {take.open_days} day{take.open_days === 1 ? "" : "s"}
                </span>
                . The longer it stays open, the more lines will have moved by
                the time you approve.
              </>
            ) : (
              <>
                {" "}
                — closed{" "}
                {take.closed_at
                  ? new Date(take.closed_at).toLocaleDateString()
                  : ""}
                .
              </>
            )}
          </p>
        </div>

        {activeTab === "count" && (
          <>
            {isOpen && (
              <StockTakeCsvPanel stockTakeId={id} onImported={invalidate} />
            )}
            {/* Scrolls inside its own container rather than pushing the page sideways — this
              is the widest table in the app. */}
            <div className="overflow-x-auto">
              <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="p-2">Item</th>
                    <th className="p-2">Expected</th>
                    <th className="p-2">Counted</th>
                    <th className="p-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((group) => {
                    const isCollapsed = collapsed.has(group.key);
                    const counted = countedInGroup(group, counts);
                    return (
                      <Fragment key={group.key}>
                        <tr className="border-b border-slate-200 bg-slate-50">
                          <th colSpan={4} className="p-2 text-left font-medium">
                            <button
                              type="button"
                              onClick={() =>
                                setCollapsed((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(group.key))
                                    next.delete(group.key);
                                  else next.add(group.key);
                                  return next;
                                })
                              }
                              className="flex w-full items-center gap-2 text-left"
                            >
                              <span className="text-slate-400">
                                {isCollapsed ? "▸" : "▾"}
                              </span>
                              <span className="text-xs uppercase tracking-wide text-slate-400">
                                {group.section}
                              </span>
                              <span>{groupLabel(group)}</span>
                              <span className="ml-auto text-xs font-normal text-slate-500">
                                counted {counted} of {group.lines.length}
                              </span>
                            </button>
                          </th>
                        </tr>
                        {!isCollapsed &&
                          group.lines.map((line) => (
                            <tr
                              key={line.id}
                              className="border-b border-slate-100"
                            >
                              <td className="p-2 pl-6">{line.name}</td>
                              <td className="p-2">
                                {roundQty(line.expected_qty)} {line.unit}
                                {/* The difference between a real variance and a shelf that only
                          looks short, so it belongs next to the number being compared. */}
                                {line.allocated_qty_at_start !== null &&
                                  Number(line.allocated_qty_at_start) > 0 && (
                                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                                      {roundQty(line.allocated_qty_at_start)}{" "}
                                      picked for orders
                                    </span>
                                  )}
                              </td>
                              <td className="p-2">
                                <input
                                  type="number"
                                  min="0"
                                  disabled={!isOpen}
                                  className="w-24 rounded border border-slate-300 px-2 py-1 disabled:bg-slate-100"
                                  placeholder="—"
                                  value={counts[line.id] ?? ""}
                                  onChange={(e) =>
                                    setCounts((prev) => ({
                                      ...prev,
                                      [line.id]: e.target.value,
                                    }))
                                  }
                                />
                              </td>
                              <td className="p-2 text-xs text-slate-500">
                                {statusLabel(line)}
                              </td>
                            </tr>
                          ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {isOpen && (
              <div className="flex items-center gap-3">
                <SaveButton
                  isDirty={isDirty}
                  isPending={saveMutation.isPending}
                  status={saveStatus}
                  onClick={() => saveMutation.mutate()}
                >
                  Save counts
                </SaveButton>
                <button
                  onClick={() => setConfirmingApprove(true)}
                  disabled={isDirty}
                  title={isDirty ? "Save your counts first" : undefined}
                  className="rounded border border-slate-300 px-4 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Review and approve
                </button>
              </div>
            )}
            <ErrorBanner error={saveMutation.error} />
          </>
        )}

        {activeTab === "review" && (
          <ReviewTab
            take={take}
            onResolve={(lineId, action) =>
              resolveMutation.mutate({ lineId, action })
            }
          />
        )}
        <ErrorBanner error={resolveMutation.error ?? approveMutation.error} />

        <ConfirmDialog
          open={confirmingApprove}
          title="Approve this stock take?"
          // Says what will happen to all three groups, because "skipped" is the one someone
          // would otherwise assume means "counted as zero".
          body={
            `${pendingApproval} counted line${pendingApproval === 1 ? "" : "s"} will be applied where nothing has ` +
            `moved since the take started. ${blanks} line${blanks === 1 ? "" : "s"} left blank will be left ` +
            `completely alone — not adjusted, and not marked as counted. Anything that moved, or that has stock ` +
            `picked for open orders, is flagged for you to settle rather than adjusted automatically. The take ` +
            `closes either way.`
          }
          confirmLabel="Approve"
          busy={approveMutation.isPending}
          onConfirm={() => approveMutation.mutate()}
          onCancel={() => setConfirmingApprove(false)}
        />
      </div>
    </DetailPanel>
  );
}

function statusLabel(line: StockTakeLine): string {
  switch (line.status) {
    case "pending":
      return "not counted";
    case "counted":
      return "counted";
    case "applied":
      return "applied";
    case "conflict":
      return "needs review";
    case "accepted_system":
      return "kept system figure";
    case "skipped":
      return "unchanged, not re-dated";
  }
}

function ReviewTab({
  take,
  onResolve,
}: {
  take: StockTakeDetail;
  onResolve: (
    lineId: number,
    action: "accept_counted" | "accept_system" | "reset",
  ) => void;
}) {
  // The conflict/non-conflict split comes first — a flagged line needs settling whatever
  // shelf it came off — and the grouping applies within each half.
  const flagged = take.lines.filter((l) => l.status === "conflict");
  const rest = take.lines.filter((l) => l.status !== "conflict");

  return (
    <div className="flex flex-col gap-4">
      {flagged.length > 0 && (
        <section>
          <h2 className="mb-1 text-lg font-semibold">Needs review</h2>
          <p className="mb-2 text-sm text-slate-500">
            These weren't adjusted. Each one is two different truths rather than
            a simple miscount, so it's your call which is right.
          </p>
          <div className="flex flex-col gap-2">
            {flagged.map((line) => (
              <div
                key={line.id}
                className="rounded border border-amber-300 bg-amber-50 p-3"
              >
                <p className="font-medium">{line.name}</p>
                <p className="text-sm text-amber-800">{line.conflict_reason}</p>
                <p className="mt-1 text-sm">
                  Expected {roundQty(line.expected_qty)} · counted{" "}
                  {line.counted_qty === null ? "—" : roundQty(line.counted_qty)}
                  {line.system_qty_at_approval !== null && (
                    <> · system now {roundQty(line.system_qty_at_approval)}</>
                  )}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    onClick={() => onResolve(line.id, "accept_counted")}
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Use the counted figure
                  </button>
                  <button
                    onClick={() => onResolve(line.id, "accept_system")}
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Keep what the system says
                  </button>
                  <button
                    onClick={() => onResolve(line.id, "reset")}
                    className="rounded border border-amber-300 bg-white px-2 py-1 text-xs"
                  >
                    Clear and count again later
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-lg font-semibold">Everything else</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Item</th>
                <th className="p-2">Expected</th>
                <th className="p-2">Counted</th>
                <th className="p-2">Difference</th>
                <th className="p-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {/* Same headings as the count sheet. Someone settling a variance is looking at
                  the same shelves they just counted, so this reads in the same order. */}
              {groupLines(rest).map((group) => (
                <Fragment key={group.key}>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    <th colSpan={5} className="p-2 text-left font-medium">
                      <span className="mr-2 text-xs uppercase tracking-wide text-slate-400">
                        {group.section}
                      </span>
                      {groupLabel(group)}
                    </th>
                  </tr>
                  {group.lines.map((line) => {
                    const delta =
                      line.delta === null ? null : Number(line.delta);
                    return (
                      <tr key={line.id} className="border-b border-slate-100">
                        <td className="p-2 pl-6">{line.name}</td>
                        <td className="p-2">{roundQty(line.expected_qty)}</td>
                        <td className="p-2">
                          {line.counted_qty === null
                            ? "—"
                            : roundQty(line.counted_qty)}
                        </td>
                        <td className={`p-2 ${delta ? "text-red-600" : ""}`}>
                          {delta === null
                            ? "—"
                            : delta > 0
                              ? `+${roundQty(line.delta!)}`
                              : roundQty(line.delta!)}
                        </td>
                        <td className="p-2 text-xs text-slate-500">
                          {statusLabel(line)}
                        </td>
                      </tr>
                    );
                  })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
