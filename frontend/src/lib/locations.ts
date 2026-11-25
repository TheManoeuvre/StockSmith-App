import type { LocationRow } from "../api/locations";

/** " › " between segments, as drawn in the design. */
export const PATH_SEPARATOR = " › ";

/** The last element — `Array.prototype.at` isn't in this project's lib target. */
export function last<T>(items: T[]): T | undefined {
  return items[items.length - 1];
}

export function pathLabel(path: string[]): string {
  return path.join(PATH_SEPARATOR);
}

/** Natural order, so "Box2" sorts before "Box10". */
export function compareNames(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

export interface TreeNode {
  row: LocationRow;
  depth: number;
  children: TreeNode[];
}

/** Builds the forest from the flat list. Archived locations are left out unless asked for. */
export function buildTree(rows: LocationRow[], includeArchived: boolean): TreeNode[] {
  const visible = rows.filter((r) => includeArchived || !r.archived);
  const byParent = new Map<number | null, LocationRow[]>();
  const ids = new Set(visible.map((r) => r.id));
  for (const r of visible) {
    // A child whose parent is hidden (archived) surfaces at the top rather than vanishing.
    const key = r.parent_id != null && ids.has(r.parent_id) ? r.parent_id : null;
    byParent.set(key, [...(byParent.get(key) ?? []), r]);
  }
  const make = (row: LocationRow, depth: number): TreeNode => ({
    row,
    depth,
    children: (byParent.get(row.id) ?? [])
      .sort((a, b) => compareNames(a.name, b.name))
      .map((c) => make(c, depth + 1)),
  });
  return (byParent.get(null) ?? []).sort((a, b) => compareNames(a.name, b.name)).map((r) => make(r, 0));
}

/** Ids of a location and everything beneath it. */
export function subtreeIds(rows: LocationRow[], id: number): Set<number> {
  const out = new Set<number>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const r of rows) {
      if (r.parent_id != null && out.has(r.parent_id) && !out.has(r.id)) {
        out.add(r.id);
        grew = true;
      }
    }
  }
  return out;
}

export function pathNames(rows: LocationRow[], id: number): string[] {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const names: string[] = [];
  const seen = new Set<number>();
  let cur: LocationRow | undefined = byId.get(id);
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    names.unshift(cur.name);
    cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined;
  }
  return names;
}

export type TreeFilter = "all" | "attention" | "empty";

export interface VisibleRow {
  node: TreeNode;
  /** A parent kept only because something beneath it matched. */
  dimmed: boolean;
}

/**
 * Which rows to draw. Search and the Attention / Empty filters keep every match *and its
 * parents*, so a match is never shown without the branch that leads to it; those parents are
 * dimmed. Collapsed branches hide their children unless a search or filter is active.
 */
export function visibleRows(
  forest: TreeNode[],
  opts: { query: string; filter: TreeFilter; expanded: Set<number> },
): VisibleRow[] {
  const q = opts.query.trim().toLowerCase();
  const filtering = q !== "" || opts.filter !== "all";
  const matches = (n: TreeNode) =>
    (q === "" || n.row.name.toLowerCase().includes(q) || n.row.code.toLowerCase() === q) &&
    (opts.filter === "all" ||
      (opts.filter === "attention" && n.row.needs_attention && n.row.own_items > 0) ||
      (opts.filter === "empty" && n.row.rolled_up_items === 0));

  const out: VisibleRow[] = [];
  const walk = (n: TreeNode): boolean => {
    const mark = out.length;
    const self = matches(n);
    out.push({ node: n, dimmed: false });
    let childHit = false;
    if (filtering || opts.expanded.has(n.row.id)) {
      for (const c of n.children) childHit = walk(c) || childHit;
    }
    if (filtering && !self && !childHit) {
      out.length = mark;
      return false;
    }
    if (filtering && !self) out[mark] = { node: n, dimmed: true };
    return self || childHit;
  };
  forest.forEach(walk);
  return out;
}

/** The ids of every location a search or filter matched outright (not merely a parent of one). */
export function matchCount(rows: VisibleRow[]): number {
  return rows.filter((r) => !r.dimmed).length;
}
