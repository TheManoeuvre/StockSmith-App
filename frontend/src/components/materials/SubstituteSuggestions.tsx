import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialSubstituteUsageApi } from "../../api/materialSubstitutes";
import type { SubstituteSuggestion } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Ranked, human-curated fallback options shown inline wherever a shortage is already
 * detected (a build-blocking BOM line, a pack-time packaging shortfall) — suggestions only,
 * never auto-applied. Picking one logs it to /material-substitute-usage so the choice is
 * traceable; it does not touch stock or the BOM itself.
 *
 * Renders nothing when there are no suggestions, so a shortage with no curated fallback
 * looks exactly like it does today.
 */
export function SubstituteSuggestions({
  materialId,
  suggestions,
  orderId,
  buildId,
}: {
  /** The short material these are fallbacks for. */
  materialId: number;
  suggestions: SubstituteSuggestion[];
  orderId?: number | null;
  buildId?: number | null;
}) {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<number | null>(null);

  const mutation = useMutation({
    mutationFn: (substituteMaterialId: number) =>
      materialSubstituteUsageApi.create({
        material_id: materialId,
        substitute_material_id: substituteMaterialId,
        order_id: orderId ?? null,
        build_id: buildId ?? null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["material-substitute-usage"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  if (suggestions.length === 0) return null;

  if (mutation.isSuccess) {
    const used = suggestions.find((s) => s.material_id === mutation.variables);
    return (
      <p className="text-[11px] font-medium text-teal-700">
        ✓ Logged — using {used?.material_name ?? "fallback"} instead
      </p>
    );
  }

  const ranked = [...suggestions].sort((a, b) => a.rank - b.rank);

  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-[11px] text-slate-400">Fallback:</span>
      {ranked.map((s) =>
        pendingId === s.material_id ? (
          <span
            key={s.material_id}
            className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-800"
          >
            Use {s.material_name}?
            <button
              type="button"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(s.material_id)}
              className="font-semibold text-blue-700 hover:text-blue-900 disabled:opacity-50"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => setPendingId(null)}
              className="text-slate-500 hover:text-slate-700"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            key={s.material_id}
            type="button"
            title={s.notes ?? undefined}
            onClick={() => setPendingId(s.material_id)}
            className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[11px] text-slate-700 hover:border-blue-400 hover:text-blue-700"
          >
            {s.material_name}{" "}
            <span className="text-slate-400 tabular-nums">({s.available_qty} avail)</span>
          </button>
        )
      )}
      <ErrorBanner error={mutation.error} />
    </div>
  );
}
