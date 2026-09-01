import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { productsApi } from "../../api/products";
import type { KittingBomLine } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { formatMoney } from "../../lib/money";
import { BomLineTable, computeLineCosts } from "./BomLineTable";

const toLines = (rows: { material_id: number; qty_required: string }[]): KittingBomLine[] =>
  rows.map((l) => ({ material_id: l.material_id, qty_required: l.qty_required }));

export function KittingBomEditor({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["products", productId, "kitting-bom"],
    queryFn: () => productsApi.getKittingBom(productId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const [filterText, setFilterText] = useState("");

  const seed = useMemo(() => (bom ? toLines(bom) : undefined), [bom]);
  const { value: lines, setValue: setLines, isDirty, markSaved } = useEditableCopy<KittingBomLine[]>({
    key: "kitting-bom",
    label: "Kitting BOM",
    initial: [],
    seed,
    seedKey: productId,
  });

  const saveMutation = useMutation({
    mutationFn: () => productsApi.replaceKittingBom(productId, lines),
    onSuccess: (rows) => {
      markSaved(toLines(rows));
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "kitting-bom"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const updateLine = (index: number, patch: Partial<KittingBomLine>) => {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => setLines((prev) => prev.filter((_, i) => i !== index));

  const saveStatus = useSaveStatus(saveMutation.status);
  const { total } = computeLineCosts(lines, materials);

  const addLine = () => {
    const firstUnused = materials?.find((m) => !lines.some((l) => l.material_id === m.id));
    if (!firstUnused) return;
    setLines((prev) => [...prev, { material_id: firstUnused.id, qty_required: "0" }]);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-md font-semibold">Kitting BOM</h3>
        {total != null && (
          <span className="text-sm font-medium tabular-nums text-slate-600">
            {formatMoney(String(total), "GBP")}
          </span>
        )}
      </div>
      <p className="text-sm text-slate-500">
        Packaging (boxes, labels, packing materials) required to pack and ship one unit — reserved when an order
        allocates, consumed only when it ships. Never consumed by recording a build.
      </p>
      {materials && materials.length > 8 && (
        <input
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Filter materials…"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
      )}
      <BomLineTable
        lines={lines}
        materials={materials}
        filterText={filterText}
        onChangeLine={updateLine}
        onRemoveLine={removeLine}
        isDirty={isDirty}
      />
      <div className="flex gap-2">
        <button onClick={addLine} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add material
        </button>
        <SaveButton
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          onClick={() => saveMutation.mutate()}
        >
          Save kitting BOM
        </SaveButton>
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
