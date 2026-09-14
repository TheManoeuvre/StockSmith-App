import type { ReactNode } from "react";

/**
 * Shared list-table chrome: a small-caps column header and a group-heading row.
 *
 * The four entity lists (Products, Materials, Orders, Purchases) each hand-roll their own
 * `<table>` — the row/filter/sort logic genuinely differs per list (server-paged vs.
 * client-filtered, different sort keys), so it isn't worth forcing into one generic table
 * component. What *is* identical across all four is this bit of chrome, so it's extracted on
 * its own rather than left copy-pasted four times.
 */
export function Th({
  children,
  onClick,
  align = "left",
}: {
  children: ReactNode;
  onClick?: () => void;
  align?: "left" | "right";
}) {
  return (
    <th
      onClick={onClick}
      className={`p-2 text-[10.5px] font-semibold uppercase tracking-wide text-slate-500 ${
        align === "right" ? "text-right" : "text-left"
      } ${onClick ? "cursor-pointer select-none hover:text-slate-700" : ""}`}
    >
      {children}
    </th>
  );
}

export function GroupHeaderRow({
  label,
  count,
  colSpan,
  capitalize = false,
  collapsed,
  onToggle,
}: {
  label: string;
  count: number;
  colSpan: number;
  /** Some legacy category values are stored lowercase — see docs/backlog.md's note on
   *  `LegacyMaterialCategory`. Capitalised for display until that data is cleaned up. */
  capitalize?: boolean;
  /** Pass both to make the header a collapse toggle for its group. Callers that omit them
   *  get the original static row unchanged. */
  collapsed?: boolean;
  onToggle?: () => void;
}) {
  const countText = `${count} ${count === 1 ? "item" : "items"}`;
  return (
    <tr className="bg-slate-100">
      <th
        colSpan={colSpan}
        className={`text-left text-[11.5px] font-semibold text-slate-700 ${capitalize ? "capitalize" : ""} ${onToggle ? "" : "p-2"}`}
      >
        {onToggle ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            className="flex w-full items-center gap-1.5 p-2 hover:bg-slate-200/70"
          >
            <span className="w-2 text-[9px] text-slate-500">
              {collapsed ? "▸" : "▾"}
            </span>
            <span>{label}</span>
            <span className="text-[10.5px] font-normal text-slate-500">
              {countText}
            </span>
          </button>
        ) : (
          <>
            {label}
            <span className="ml-2 text-[10.5px] font-normal text-slate-500">
              {countText}
            </span>
          </>
        )}
      </th>
    </tr>
  );
}
