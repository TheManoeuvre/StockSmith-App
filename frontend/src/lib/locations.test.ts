import { describe, expect, it } from "vitest";
import type { LocationRow } from "../api/locations";
import { buildTree, compareNames, pathLabel, pathNames, subtreeIds, visibleRows } from "./locations";

let nextId = 1;
const loc = (name: string, parent: number | null, o: Partial<LocationRow> = {}): LocationRow => ({
  id: nextId++,
  parent_id: parent,
  name,
  code: `LOC-${String(nextId).padStart(6, "0")}`,
  notes: null,
  archived: false,
  own_items: 0,
  rolled_up_items: 0,
  needs_attention: false,
  ...o,
});

// Workshop > (Wax shelf, Oils rack > (Bin 1, Bin 2, Bin 10)) ; Loft ; Old shed (archived)
function sample() {
  nextId = 1;
  const workshop = loc("Workshop", null, { rolled_up_items: 9 });
  const wax = loc("Wax shelf", workshop.id, { own_items: 3, rolled_up_items: 3 });
  const oils = loc("Oils rack", workshop.id, { rolled_up_items: 6 });
  const bin10 = loc("Bin 10", oils.id, { own_items: 1, rolled_up_items: 1 });
  const bin2 = loc("Bin 2", oils.id, { own_items: 2, rolled_up_items: 2, needs_attention: true });
  const bin1 = loc("Bin 1", oils.id, { own_items: 3, rolled_up_items: 3 });
  const loft = loc("Loft", null);
  const shed = loc("Old shed", null, { archived: true });
  return { rows: [workshop, wax, oils, bin10, bin2, bin1, loft, shed], workshop, wax, oils, bin1, bin2, bin10, loft, shed };
}

describe("compareNames", () => {
  it("sorts numbers inside names naturally", () => {
    expect(["Box10", "Box2", "Box1"].sort(compareNames)).toEqual(["Box1", "Box2", "Box10"]);
  });
});

describe("buildTree", () => {
  it("nests children under parents in natural order and hides archived ones by default", () => {
    const { rows } = sample();
    const forest = buildTree(rows, false);
    expect(forest.map((n) => n.row.name)).toEqual(["Loft", "Workshop"]);
    const oils = forest[1].children.find((c) => c.row.name === "Oils rack")!;
    expect(oils.children.map((c) => c.row.name)).toEqual(["Bin 1", "Bin 2", "Bin 10"]);
    expect(oils.children[0].depth).toBe(2);
  });

  it("shows archived locations when asked", () => {
    expect(buildTree(sample().rows, true).map((n) => n.row.name)).toContain("Old shed");
  });

  it("surfaces a child whose archived parent is hidden rather than losing it", () => {
    nextId = 1;
    const shed = loc("Old shed", null, { archived: true });
    const orphan = loc("Crate", shed.id);
    expect(buildTree([shed, orphan], false).map((n) => n.row.name)).toEqual(["Crate"]);
  });
});

describe("visibleRows", () => {
  const opts = (o: Partial<Parameters<typeof visibleRows>[1]> = {}) => ({
    query: "",
    filter: "all" as const,
    expanded: new Set<number>(),
    ...o,
  });

  it("shows only the top level when nothing is expanded", () => {
    const { rows } = sample();
    expect(visibleRows(buildTree(rows, false), opts()).map((r) => r.node.row.name)).toEqual(["Loft", "Workshop"]);
  });

  it("shows children of expanded branches", () => {
    const { rows, workshop } = sample();
    const names = visibleRows(buildTree(rows, false), opts({ expanded: new Set([workshop.id]) })).map(
      (r) => r.node.row.name,
    );
    expect(names).toEqual(["Loft", "Workshop", "Oils rack", "Wax shelf"]);
  });

  it("keeps the parents of a search match, dimmed, even when collapsed", () => {
    const { rows } = sample();
    const result = visibleRows(buildTree(rows, false), opts({ query: "bin 2" }));
    expect(result.map((r) => [r.node.row.name, r.dimmed])).toEqual([
      ["Workshop", true],
      ["Oils rack", true],
      ["Bin 2", false],
    ]);
  });

  it("matches a scan code exactly", () => {
    const { rows, loft } = sample();
    const result = visibleRows(buildTree(rows, false), opts({ query: loft.code.toLowerCase() }));
    expect(result.map((r) => r.node.row.name)).toEqual(["Loft"]);
  });

  it("the Attention filter keeps locations that hold drifting stock, with their parents", () => {
    const { rows } = sample();
    const result = visibleRows(buildTree(rows, false), opts({ filter: "attention" }));
    expect(result.map((r) => r.node.row.name)).toEqual(["Workshop", "Oils rack", "Bin 2"]);
  });

  it("the Empty filter finds locations with nothing in or under them", () => {
    const { rows } = sample();
    const result = visibleRows(buildTree(rows, false), opts({ filter: "empty" }));
    expect(result.filter((r) => !r.dimmed).map((r) => r.node.row.name)).toEqual(["Loft"]);
  });
});

describe("paths and subtrees", () => {
  it("builds a path from the root down", () => {
    const { rows, bin1 } = sample();
    expect(pathNames(rows, bin1.id)).toEqual(["Workshop", "Oils rack", "Bin 1"]);
    expect(pathLabel(pathNames(rows, bin1.id))).toBe("Workshop › Oils rack › Bin 1");
  });

  it("collects a location and everything beneath it", () => {
    const { rows, oils, bin1, bin2, bin10, wax } = sample();
    const ids = subtreeIds(rows, oils.id);
    expect([...ids].sort()).toEqual([oils.id, bin1.id, bin2.id, bin10.id].sort());
    expect(ids.has(wax.id)).toBe(false);
  });
});
