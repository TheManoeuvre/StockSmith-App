import type { StockTakeLine } from "../../api/types";

/**
 * Turn the server's already-ordered lines into the headings a table renders.
 *
 * The *order* is the backend's — it comes off the wire arranged, and re-sorting here would
 * be a second opinion that could disagree with the printed CSV. This only walks that order
 * and notices where the headings change, which is why it takes one pass and no comparator.
 */
export interface LineGroup {
  /** "Products" / "Materials", shown once above the groups it contains. */
  section: string;
  /** Product category, or material category. */
  group: string;
  /** Parent SKU, or material type. Blank for things that have none. */
  subgroup: string;
  key: string;
  lines: StockTakeLine[];
}

export function groupLines(lines: StockTakeLine[]): LineGroup[] {
  const out: LineGroup[] = [];
  for (const line of lines) {
    const last = out[out.length - 1];
    if (last && last.section === line.section && last.group === line.group && last.subgroup === line.subgroup) {
      last.lines.push(line);
      continue;
    }
    out.push({
      section: line.section,
      group: line.group,
      subgroup: line.subgroup,
      key: `${line.section}|${line.group}|${line.subgroup}`,
      lines: [line],
    });
  }
  return out;
}

/** "Uncategorised" beats an empty heading, which reads as a rendering bug. */
export function groupLabel(group: LineGroup): string {
  const category = group.group || "Uncategorised";
  return group.subgroup ? `${category} · ${group.subgroup}` : category;
}

export function countedInGroup(group: LineGroup, counts: Record<number, string>): number {
  return group.lines.filter((l) => (counts[l.id] ?? "") !== "").length;
}
