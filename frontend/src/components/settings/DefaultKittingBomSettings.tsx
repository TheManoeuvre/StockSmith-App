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

const toLines = (rows: { material_id: number; qty_required: string }[]): KittingBomLine[] =>
  rows.map((l) => ({ material_id: l.material_id, qty_required: l.qty_required }));

export function DefaultKittingBomSettings() {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["settings", "default-kitting-bom"],
    queryFn: appSettingsApi.getDefaultKittingBom,
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const [filterText, setFilterText] = useState("");

  // No DirtyRegistryProvider on the settings route, so useEditableCopy's registration is a
  // no-op here — this gets the seed-once fix and the disabled-until-dirty button, but no
  // unsaved-changes prompt. That's deliberate; the guard is a product-page feature.
  const seed = useMemo(() => (bom ? toLines(bom) : undefined), [bom]);
  const { value: lines, setValue: setLines, isDirty, markSaved } = useEditableCopy<KittingBomLine[]>({
    key: "default-kitting-bom",
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
    const firstUnused = materials?.find((m) => !lines.some((l) => l.material_id === m.id));
    if (!firstUnused) return;
    setLines((prev) => [...prev, { material_id: firstUnused.id, qty_required: "0" }]);
  };

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Default kitting BOM</p>
        <p className="text-sm text-slate-500">
          Packaging materials (box, label, tape) automatically added to every new product's kitting BOM when it's
          created — pick materials you already track stock for. This is a one-time snapshot: changing it here only
          affects products created afterward, not ones that already exist.
        </p>
      </div>
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
    </div>
  );
}
