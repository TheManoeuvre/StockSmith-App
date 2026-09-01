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
}: {
  label: string;
  count: number;
  colSpan: number;
  /** Some legacy category values are stored lowercase — see docs/backlog.md's note on
   *  `LegacyMaterialCategory`. Capitalised for display until that data is cleaned up. */
  capitalize?: boolean;
}) {
  return (
    <tr className="bg-slate-100">
      <th
        colSpan={colSpan}
        className={`p-2 text-left text-[11.5px] font-semibold text-slate-700 ${capitalize ? "capitalize" : ""}`}
      >
        {label}
        <span className="ml-2 text-[10.5px] font-normal text-slate-500">
          {count} {count === 1 ? "item" : "items"}
        </span>
      </th>
    </tr>
  );
}
