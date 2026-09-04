import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { appSettingsApi } from "../../api/appSettings";
import { materialsApi } from "../../api/materials";
import type { KittingBomLine } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { BomLineTable } from "../products/BomLineTable";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { SettingsCard } from "./SettingsCard";

const toLines = (rows: { material_id: number; qty_required: string }[]): KittingBomLine[] =>
  rows.map((l) => ({ material_id: l.material_id, qty_required: l.qty_required }));

export function DefaultKittingBomSettings() {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["settings", "default-kitting-bom"],
    queryFn: appSettingsApi.getDefaultKittingBom,
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { kittingBomCategoryNames } = useMaterialCategories();

  const [filterText, setFilterText] = useState("");

  const seed = useMemo(() => (bom ? toLines(bom) : undefined), [bom]);
  const { value: lines, setValue: setLines, isDirty, markSaved } = useEditableCopy<KittingBomLine[]>({
    key: "shipping-packaging/default-kitting-bom",
    label: "Default kitting BOM",
    initial: [],
    seed,
    seedKey: "settings",
  });

  const saveMutation = useMutation({
    mutationFn: () => appSettingsApi.replaceDefaultKittingBom(lines),
    onSuccess: (rows) => {
      markSaved(toLines(rows));
      queryClient.invalidateQueries({ queryKey: ["settings", "default-kitting-bom"] });
    },
  });

  const updateLine = (index: number, patch: Partial<KittingBomLine>) => {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => setLines((prev) => prev.filter((_, i) => i !== index));

  const saveStatus = useSaveStatus(saveMutation.status);

  const addLine = () => {
    // Default into a kitting-flagged category — the picker hides the rest, so landing on
    // materials[0] (often filament) would show that row out of scope. Fall back to any unused
    // material only when nothing is flagged yet, so the button still works.
    const unused = (m: { id: number }) => !lines.some((l) => l.material_id === m.id);
    const firstUnused =
      materials?.find((m) => unused(m) && kittingBomCategoryNames.has(m.category)) ??
      materials?.find(unused);
    if (!firstUnused) return;
    setLines((prev) => [...prev, { material_id: firstUnused.id, qty_required: "0" }]);
  };

  return (
    <SettingsCard
      title="Default kitting BOM"
      help={
        <>
          Packaging materials (box, label, tape) automatically added to every new product's kitting BOM when
          it's created — pick materials you already track stock for. This is a one-time snapshot: changing it
          here only affects products created afterward, not ones that already exist. Whether a material is
          consumed once per order rather than once per unit is set per material category, under Stock → Lists →
          Material categories ("Kitting: one per order, not per unit").
        </>
      }
    >
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
        // No stock context here — this list isn't attached to any product.
        showMaxFromFreeStock={false}
        isDirty={isDirty}
        kittingOnly
        tableClassName="w-full table-fixed border-collapse bg-white text-left text-sm"
      />
      {lines.length === 0 && <p className="text-sm text-slate-400">No default kitting materials configured.</p>}
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
          Save
        </SaveButton>
      </div>
      <ErrorBanner error={saveMutation.error} />
    </SettingsCard>
  );
}
