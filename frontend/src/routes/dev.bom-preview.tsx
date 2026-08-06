import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import type { Material } from "../api/types";
import { BomLineTable, type BomLineTableRow } from "../components/products/BomLineTable";

/**
 * Dev-only visual harness for BomLineTable. Renders the two stacked tables with fixture
 * data and no backend, so their column alignment and cost/share/total rendering can be
 * inspected (and screenshotted) without a configured connection or a seeded database.
 *
 * Deliberately uses material names of very different lengths in the two tables: column
 * alignment used to be incidental — content sized the columns, so the tables only lined up
 * while their longest names happened to match. If the colgroup ever regresses, this page
 * shows it immediately.
 *
 * Renders nothing outside `vite dev`. The module is tiny and tree-shakes to a stub in a
 * production build; it is not linked from anywhere in the app.
 */
export const Route = createFileRoute("/dev/bom-preview")({
  component: import.meta.env.DEV ? BomPreview : () => null,
});

const material = (
  id: number,
  name: string,
  avg_unit_cost: string,
  current_qty: string,
  allocated_qty: string,
  unit = "each"
): Material =>
  ({ id, name, avg_unit_cost, current_qty, allocated_qty, unit, category: "other" }) as unknown as Material;

const MATERIALS: Material[] = [
  material(1, "PLA+ Filament", "0.024", "4500", "500", "g"),
  material(2, "M3x8 Socket Head Cap Screw", "0.031", "800", "120"),
  material(3, "Neodymium Magnet 6x2mm", "0.088", "300", "40"),
  material(4, "Prem Postal Box — 217x108x50 mm", "0.295", "140", "20"),
  material(5, "4x6 Direct Thermal Label", "0.011", "2000", "0"),
];

const BUILD_BOM: BomLineTableRow[] = [
  { material_id: 1, qty_required: "42" },
  { material_id: 2, qty_required: "4" },
  { material_id: 3, qty_required: "2" },
];

const KITTING_BOM: BomLineTableRow[] = [
  { material_id: 4, qty_required: "1" },
  { material_id: 5, qty_required: "1" },
];

function BomPreview() {
  const [build, setBuild] = useState(BUILD_BOM);
  const [kitting, setKitting] = useState(KITTING_BOM);
  const [buildDirty, setBuildDirty] = useState(false);

  const editRow =
    (setRows: typeof setBuild, markDirty?: () => void) => (index: number, patch: Partial<BomLineTableRow>) => {
      setRows((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
      markDirty?.();
    };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">BomLineTable preview</h1>
        <p className="text-sm text-slate-500">
          Dev-only fixture page — no backend. The two tables below must have identical column
          edges despite very different content widths.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-md font-semibold">Build BOM</h3>
        <BomLineTable
          lines={build}
          materials={MATERIALS}
          onChangeLine={editRow(setBuild, () => setBuildDirty(true))}
          onRemoveLine={(i) => setBuild((prev) => prev.filter((_, x) => x !== i))}
          isDirty={buildDirty}
        />
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-md font-semibold">Kitting BOM</h3>
        <BomLineTable
          lines={kitting}
          materials={MATERIALS}
          onChangeLine={editRow(setKitting)}
          onRemoveLine={(i) => setKitting((prev) => prev.filter((_, x) => x !== i))}
        />
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-md font-semibold">Settings variant (no free-stock column)</h3>
        <BomLineTable
          lines={kitting}
          materials={MATERIALS}
          onChangeLine={editRow(setKitting)}
          onRemoveLine={(i) => setKitting((prev) => prev.filter((_, x) => x !== i))}
          showMaxFromFreeStock={false}
          tableClassName="w-full table-fixed border-collapse bg-white text-left text-sm"
        />
      </div>
    </div>
  );
}
