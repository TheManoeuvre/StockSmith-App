import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import type { Material } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";

/**
 * Merge this material into another: pick the survivor, see every table that will be
 * touched and where quantities will be added together, confirm.
 *
 * Unlike the reference-data merge in Settings this shows a preview first, because a
 * material is referenced from a dozen places with their own quantities and the user
 * should see "2 BOM lines will be summed" before rather than after.
 */
export function MaterialMergeModal({
  source,
  initialTargetId,
  onClose,
  onMerged,
}: {
  source: Material;
  initialTargetId?: number;
  onClose: () => void;
  onMerged: (target: Material) => void;
}) {
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const [targetId, setTargetId] = useState<number>(initialTargetId ?? 0);
  const [filterText, setFilterText] = useState("");

  const needle = filterText.trim().toLowerCase();
  const candidates = (materials ?? []).filter(
    (m) =>
      m.id !== source.id &&
      m.unit === source.unit &&
      (m.is_active || m.id === targetId) &&
      (!needle || m.name.toLowerCase().includes(needle) || (m.barcode ?? "").toLowerCase().includes(needle)),
  );
  const byCategory = new Map<string, Material[]>();
  for (const m of candidates) byCategory.set(m.category, [...(byCategory.get(m.category) ?? []), m]);

  const preview = useQuery({
    queryKey: ["materials", source.id, "merge-preview", targetId],
    queryFn: () => materialsApi.previewMerge(source.id, targetId),
    enabled: targetId > 0,
    retry: false,
  });
  const plan = preview.data;

  const mergeMutation = useMutation({
    mutationFn: () => materialsApi.merge(source.id, targetId),
    onSuccess: (target) => {
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      onMerged(target);
    },
  });

  const canMerge = plan != null && plan.blockers.length === 0 && !mergeMutation.isPending;
  const unitLabel = source.unit;

  return (
    <Modal
      title={`Merge "${source.name}" into…`}
      maxWidth="max-w-2xl"
      onClose={mergeMutation.isPending ? () => {} : onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5">
            Cancel
          </button>
          <button
            onClick={() => mergeMutation.mutate()}
            disabled={!canMerge}
            className="rounded-md bg-red-600 px-4 py-1.5 text-white disabled:opacity-50"
          >
            {mergeMutation.isPending ? "Merging…" : "Merge"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4 text-sm">
        <div className="flex flex-col gap-1">
          <span>Survivor — the material that keeps everything</span>
          <input
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="Filter…"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
          />
          <select
            aria-label="Survivor"
            className="rounded border border-slate-300 px-2 py-1"
            value={targetId}
            onChange={(e) => setTargetId(Number(e.target.value))}
          >
            <option value={0}>Choose…</option>
            {Array.from(byCategory.entries()).map(([category, list]) => (
              <optgroup key={category} label={category} className="capitalize">
                {list.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <p className="text-xs text-slate-500">Only materials counted in {unitLabel} are offered.</p>
        </div>

        {preview.isPending && targetId > 0 && <p className="text-slate-500">Checking what would change…</p>}
        <ErrorBanner error={preview.error} />

        {plan && (
          <>
            {plan.blockers.map((b) => (
              <p key={b} className="rounded bg-red-50 p-2 text-red-700">
                {b}
              </p>
            ))}
            <div className="rounded border border-slate-200 bg-slate-50 p-2">
              <p>
                Stock becomes <strong>{plan.combined_qty}</strong> {unitLabel}, with the unit cost re-averaged across
                both purchase histories.
              </p>
              {plan.effects.length === 0 ? (
                <p className="mt-1 text-slate-600">Nothing else refers to "{plan.source_name}".</p>
              ) : (
                <ul className="mt-1 flex flex-col gap-0.5">
                  {plan.effects.map((e) => (
                    <li key={e.label}>
                      {e.repointed > 0 && (
                        <>
                          {e.repointed} {e.label} will point at "{plan.target_name}"
                        </>
                      )}
                      {e.repointed > 0 && e.summed > 0 && "; "}
                      {e.summed > 0 && (
                        <span className="text-amber-800">
                          {e.summed} {e.label} already had "{plan.target_name}" — quantities will be added together
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="text-xs text-slate-500">
              "{plan.source_name}" will be deleted. Its purchases, adjustments and stock-take history move to "
              {plan.target_name}" and stay readable there. This can't be undone.
            </p>
          </>
        )}

        <ErrorBanner error={mergeMutation.error} />
      </div>
    </Modal>
  );
}
