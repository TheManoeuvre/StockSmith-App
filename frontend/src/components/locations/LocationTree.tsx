import { useMemo, useState } from "react";
import type { LocationRow } from "../../api/locations";
import { buildTree, visibleRows, type TreeFilter } from "../../lib/locations";

function Chevron({ open, hidden }: { open: boolean; hidden: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`h-3.5 w-3.5 flex-none text-slate-500 transition-transform ${open ? "rotate-90" : ""} ${hidden ? "invisible" : ""}`}
    >
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  );
}

/**
 * The location tree — the Locations page's left pane, and the body of every location picker.
 *
 * Counts roll up, so a collapsed branch still says what's inside. Search and filters keep the
 * parents of whatever matched (dimmed), so a result is never shown without the branch that leads
 * to it. `disabled` marks rows that can't be chosen and says why ("it's inside Box01") — used by
 * the move dialogs.
 */
export function LocationTree({
  rows,
  selectedId,
  onSelect,
  query = "",
  filter = "all",
  showArchived = false,
  disabled,
  defaultExpandAll = false,
  ariaLabel = "Locations",
}: {
  rows: LocationRow[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  query?: string;
  filter?: TreeFilter;
  showArchived?: boolean;
  /** Rows that can't be chosen, each with the reason shown beside it. */
  disabled?: Map<number, string>;
  defaultExpandAll?: boolean;
  ariaLabel?: string;
}) {
  const forest = useMemo(() => buildTree(rows, showArchived), [rows, showArchived]);
  const [expanded, setExpanded] = useState<Set<number>>(
    () => new Set(defaultExpandAll ? rows.map((r) => r.id) : rows.filter((r) => r.parent_id == null).map((r) => r.id)),
  );
  // Make sure the selected location's ancestors are open, so selecting from elsewhere (a
  // breadcrumb, a scan) never leaves the selection hidden inside a collapsed branch.
  const open = useMemo(() => {
    const next = new Set(expanded);
    const byId = new Map(rows.map((r) => [r.id, r]));
    let cur = selectedId != null ? byId.get(selectedId) : undefined;
    while (cur?.parent_id != null) {
      next.add(cur.parent_id);
      cur = byId.get(cur.parent_id);
    }
    return next;
  }, [expanded, rows, selectedId]);
  const shown = useMemo(
    () => visibleRows(forest, { query, filter, expanded: open }),
    [forest, query, filter, open],
  );

  // A branch held open only because the selection is inside it stays open: closing it would
  // hide the selection.
  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (open.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (shown.length === 0) {
    return <p className="px-2 py-3 text-xs text-slate-500">No locations match.</p>;
  }

  return (
    <div role="tree" aria-label={ariaLabel} className="flex flex-col gap-px">
      {shown.map(({ node, dimmed }) => {
        const { row, depth, children } = node;
        const reason = disabled?.get(row.id);
        const isSelected = row.id === selectedId;
        const isOpen = open.has(row.id);
        return (
          <div
            key={row.id}
            role="treeitem"
            aria-level={depth + 1}
            aria-selected={isSelected}
            aria-expanded={children.length ? isOpen : undefined}
            aria-disabled={reason ? true : undefined}
            className={`flex h-7 items-center gap-1.5 rounded-md pr-2.5 text-[13px] ${
              isSelected ? "bg-slate-100 font-medium text-slate-900" : "text-slate-700"
            } ${dimmed || reason ? "text-slate-400" : ""} ${reason ? "cursor-not-allowed" : "hover:bg-slate-50"}`}
            style={{ paddingLeft: 4 + depth * 16 }}
          >
            <button
              type="button"
              aria-label={isOpen ? `Collapse ${row.name}` : `Expand ${row.name}`}
              tabIndex={children.length ? 0 : -1}
              onClick={() => toggle(row.id)}
              className="flex-none"
            >
              <Chevron open={isOpen} hidden={children.length === 0} />
            </button>
            <button
              type="button"
              disabled={!!reason}
              onClick={() => onSelect(row.id)}
              className={`flex min-w-0 flex-1 items-center gap-1.5 text-left ${row.archived ? "italic text-slate-500" : ""}`}
            >
              <span className="truncate">{row.name}</span>
              {row.archived && <span className="text-[10.5px] text-slate-400">archived</span>}
              {row.needs_attention && (
                <span
                  role="img"
                  aria-label="Needs attention"
                  title="Needs attention"
                  className="h-[7px] w-[7px] flex-none rounded-full bg-amber-500"
                />
              )}
              {reason ? (
                <span className="ml-auto text-[11.5px] text-slate-500">{reason}</span>
              ) : (
                <span className="ml-auto text-[11.5px] tabular-nums text-slate-500">{row.rolled_up_items}</span>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
