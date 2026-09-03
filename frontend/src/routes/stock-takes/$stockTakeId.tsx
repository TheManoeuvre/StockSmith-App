import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useMemo, useState } from "react";
import { stockTakesApi } from "../../api/stockTakes";
import type { StockTakeDetail, StockTakeLine } from "../../api/types";
import { Badge } from "../../components/common/Badge";
import { ConfirmDialog } from "../../components/common/ConfirmDialog";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { Stat } from "../../components/common/Stat";
import { StockTakeCsvPanel } from "../../components/stockTakes/StockTakeCsvPanel";
import {
  countedInGroup,
  groupLabel,
  groupLines,
} from "../../components/stockTakes/groupLines";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import {
  SlideOverManagedContext,
  useCommittableDirty,
  useDirtyRegistryApi,
  useManagedSave,
} from "../../hooks/useDirtyRegistry";
import { useSiblingNav } from "../../hooks/useSiblingNav";
import { formatDayMonth, roundQty } from "../../lib/format";

export const Route = createFileRoute("/stock-takes/$stockTakeId")({
  component: StockTakeDetailRoute,
});

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

// The footer replaces the inline Save-counts bar with one managed Save/Revert; the context
// is provided a layer above the body so the count-sheet's useManagedSave can read it.
function StockTakeDetailRoute() {
  return (
    <SlideOverManagedContext.Provider value={true}>
      <StockTakeDetailPage />
    </SlideOverManagedContext.Provider>
  );
}

function StockTakeDetailPage() {
  const { stockTakeId } = Route.useParams();
  const id = Number(stockTakeId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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
    markSaved,
    revert: revertCounts,
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
  useManagedSave("count-sheet", {
    save: () => saveMutation.mutate(),
    revert: revertCounts,
  });
  const { isDirty: committableDirty } = useCommittableDirty();
  const registry = useDirtyRegistryApi();

  const approveMutation = useMutation({
    mutationFn: () => stockTakesApi.approve(id),
    onSuccess: () => {
      setConfirmingApprove(false);
      invalidate();
    },
  });

  if (!take) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  const isOpen = take.status === "open";
  const countedLines = take.lines.filter((l) => l.status === "counted");
  const pendingApproval = countedLines.length;
  // Split the counted lines by whether the number entered differs from the snapshot, so the
  // approve dialog can say what will actually be written rather than implying a no-op.
  const changingLines = countedLines.filter(
    (l) =>
      l.counted_qty !== null &&
      Number(l.counted_qty) !== Number(l.expected_qty),
  ).length;
  const confirmingLines = pendingApproval - changingLines;
  const blanks = take.lines.filter(
    (l) => l.counted_qty === null && l.status === "pending",
  ).length;

  const productLines = take.lines.filter(
    (l) => l.product_id !== null || l.variant_id !== null,
  ).length;
  const appliedLines = take.lines.filter((l) => l.status === "applied").length;

  const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
  const approveBody =
    pendingApproval === 0
      ? `Nothing has been counted, so approving closes the take without changing any stock. ` +
        `${blanks} blank ${plural(blanks, "line is", "lines are")} left alone.`
      : `Approving writes your counts to stock. Of ${pendingApproval} counted ` +
        `${plural(pendingApproval, "line", "lines")}, ${changingLines} ` +
        `${plural(changingLines, "adjusts", "adjust")} the recorded quantity and ${confirmingLines} ` +
        `${plural(confirmingLines, "confirms", "confirm")} it unchanged. ${blanks} blank ` +
        `${plural(blanks, "line is", "lines are")} left alone — not adjusted, not marked counted. ` +
        `Any line whose system stock has moved since the take started, or that has units allocated ` +
        `to open orders, is flagged for you to settle rather than adjusted automatically. The take ` +
        `closes either way.`;

  return (
    <DetailPanel
      title={`Stock take #${take.id}`}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      footer={
        isOpen ? (
          <div className="flex items-center justify-between gap-3">
            <button
              onClick={() => setConfirmingApprove(true)}
              disabled={committableDirty}
              title={committableDirty ? "Save your counts first" : undefined}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              Review and approve
            </button>
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-slate-500">
                {committableDirty ? "Unsaved changes" : "No changes"}
              </span>
              <button
                type="button"
                disabled={!committableDirty}
                onClick={() => registry.revertDirtyUnder("")}
                className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                Revert
              </button>
              <button
                type="button"
                disabled={!committableDirty}
                onClick={() => registry.commitDirtyUnder("")}
                className="rounded bg-slate-900 px-4 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Save counts
              </button>
            </div>
          </div>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-3">
          <p className="text-[12.5px] text-slate-500">
            {take.scope_description} · started {formatDayMonth(take.started_at)} ·{" "}
            {isOpen ? (
              <span className="text-amber-700">
                open {take.open_days} day{take.open_days === 1 ? "" : "s"}
              </span>
            ) : (
              <>closed {take.closed_at ? formatDayMonth(take.closed_at) : ""}</>
            )}
          </p>
          <div className="grid grid-cols-4 gap-3">
            <Stat
              label="Counted"
              value={`${take.completed_count} / ${take.line_count}`}
              sub={`${productLines} product · ${take.line_count - productLines} material`}
            />
            <Stat
              label={isOpen ? "To apply" : "Applied"}
              value={String(isOpen ? pendingApproval : appliedLines)}
              sub={isOpen ? "on approval" : "written to stock"}
            />
            <Stat
              label="Needs review"
              value={String(take.conflict_count)}
              sub="moved since snapshot"
              valueClassName={take.conflict_count > 0 ? "text-amber-700" : undefined}
            />
            <Stat
              label="Pending"
              value={String(take.pending_count)}
              sub="not counted yet"
            />
          </div>
        </div>

        {take.conflict_count > 0 && (
          <Link
            to="/stock-takes/unresolved"
            className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800"
          >
            {take.conflict_count} line{take.conflict_count === 1 ? "" : "s"} flagged for review
            — settle {take.conflict_count === 1 ? "it" : "them"} on Unresolved variances →
          </Link>
        )}

        {isOpen && <StockTakeCsvPanel stockTakeId={id} onImported={invalidate} />}
        {/* Scrolls inside its own container rather than pushing the page sideways — this
            is the widest table in the app. */}
        <div className="overflow-x-auto">
              <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/60">
                    <th className="p-2">Item</th>
                    <th className="p-2 text-right">Expected</th>
                    <th className="p-2 text-right">Counted</th>
                    <th className="p-2 text-right">Delta</th>
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
                          <th colSpan={5} className="p-2 text-left font-medium">
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
                          group.lines.map((line) => {
                            const typed = (counts[line.id] ?? "").trim();
                            const liveDelta =
                              typed === ""
                                ? null
                                : Number(typed) - Number(line.expected_qty);
                            return (
                              <tr
                                key={line.id}
                                className="border-b border-slate-100 last:border-0"
                              >
                                <td className="p-2 pl-6">{line.name}</td>
                                <td className="p-2 text-right tabular-nums">
                                  {roundQty(line.expected_qty)} {line.unit}
                                  {/* A real variance vs. a shelf that only looks short —
                                  belongs next to the number being compared. */}
                                  {line.allocated_qty_at_start !== null &&
                                    Number(line.allocated_qty_at_start) > 0 && (
                                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800">
                                        {roundQty(line.allocated_qty_at_start)}{" "}
                                        picked for orders
                                      </span>
                                    )}
                                </td>
                                <td className="p-2 text-right">
                                  <input
                                    type="number"
                                    min="0"
                                    disabled={!isOpen}
                                    className="w-20 rounded border border-slate-300 px-2 py-1 text-right tabular-nums disabled:bg-slate-100"
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
                                <td
                                  className={`p-2 text-right tabular-nums ${
                                    liveDelta == null || liveDelta === 0
                                      ? "text-slate-400"
                                      : "text-amber-700"
                                  }`}
                                >
                                  {liveDelta == null
                                    ? "—"
                                    : liveDelta > 0
                                      ? `+${roundQty(String(liveDelta))}`
                                      : roundQty(String(liveDelta))}
                                </td>
                                <td className="p-2">
                                  <StatusBadge status={line.status} />
                                </td>
                              </tr>
                            );
                          })}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

        <ErrorBanner error={saveMutation.error} />
        <ErrorBanner error={approveMutation.error} />

        <ConfirmDialog
          open={confirmingApprove}
          title="Approve this stock take?"
          // Spells out what approving writes rather than implying a no-op: counted lines are
          // applied (some adjust a figure, some confirm it), blanks are left alone, and moved
          // or allocated lines are held back for review.
          body={approveBody}
          confirmLabel="Approve"
          busy={approveMutation.isPending}
          onConfirm={() => approveMutation.mutate()}
          onCancel={() => setConfirmingApprove(false)}
        />
      </div>
    </DetailPanel>
  );
}

const STATUS_META: Record<
  StockTakeLine["status"],
  { label: string; cls: string }
> = {
  pending: { label: "Not counted", cls: "bg-slate-100 text-slate-500" },
  counted: { label: "Counted", cls: "bg-blue-100 text-blue-800" },
  applied: { label: "Auto-applied", cls: "bg-green-100 text-green-800" },
  conflict: { label: "Needs review", cls: "bg-amber-100 text-amber-800" },
  accepted_system: { label: "Kept system", cls: "bg-slate-100 text-slate-600" },
  skipped: { label: "Unchanged", cls: "bg-slate-100 text-slate-500" },
};

function StatusBadge({ status }: { status: StockTakeLine["status"] }) {
  const meta = STATUS_META[status];
  return <Badge className={meta.cls}>{meta.label}</Badge>;
}
