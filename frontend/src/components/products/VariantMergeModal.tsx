import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { variantsApi } from "../../api/variants";
import type {
  LiveListingResolution,
  MergeBomChoice,
  MergeBomLine,
  Variant,
  VariantMergePlan,
  VariantMergeResult,
} from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LiveListingConflictDialog, liveListingConflictDetail } from "./LiveListingConflictDialog";

/**
 * Merge one variant into a sibling: pick the survivor, see what will move, choose which
 * recipe wins if they differ, confirm.
 *
 * The preview is fetched as soon as a target is chosen rather than behind a button,
 * because there is nothing else to fill in — the choices (which BOM to keep) only make
 * sense once the preview has shown that they differ.
 */
export function VariantMergeModal({
  loser,
  siblings,
  onClose,
  onMerged,
}: {
  loser: Variant;
  /** Every other active variant of the same product — the possible survivors. */
  siblings: Variant[];
  onClose: () => void;
  onMerged: (result: VariantMergeResult) => void;
}) {
  const queryClient = useQueryClient();
  const [targetId, setTargetId] = useState<number | null>(siblings.length === 1 ? siblings[0].id : null);
  const [bom, setBom] = useState<MergeBomChoice>("keep_survivor");
  const [kitting, setKitting] = useState<MergeBomChoice>("keep_survivor");
  const [result, setResult] = useState<VariantMergeResult | null>(null);

  const preview = useQuery({
    queryKey: ["variants", loser.id, "merge-preview", targetId],
    queryFn: () => variantsApi.previewMerge(loser.id, targetId as number),
    enabled: targetId != null && result == null,
    retry: false,
  });
  const plan = preview.data;

  const mergeMutation = useMutation({
    mutationFn: (onLiveListing: LiveListingResolution) =>
      variantsApi.merge(loser.id, { target_id: targetId as number, bom, kitting, on_live_listing: onLiveListing }),
    onSuccess: (merged) => {
      setResult(merged);
      queryClient.invalidateQueries({ queryKey: ["products", loser.product_id, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", loser.product_id] });
      queryClient.invalidateQueries({ queryKey: ["variants"] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      onMerged(merged);
    },
  });
  const liveConflict = liveListingConflictDetail(mergeMutation.error);

  const blocked = (plan?.blockers.length ?? 0) > 0;
  const canMerge = plan != null && !blocked && !mergeMutation.isPending;

  return (
    <Modal
      title={`Merge "${loser.variant_name}" into…`}
      maxWidth="max-w-3xl"
      onClose={mergeMutation.isPending ? () => {} : onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5">
            {result ? "Close" : "Cancel"}
          </button>
          {!result && (
            <button
              onClick={() => mergeMutation.mutate("ask")}
              disabled={!canMerge}
              className="rounded-md bg-red-600 px-4 py-1.5 text-white disabled:opacity-50"
            >
              {mergeMutation.isPending ? "Merging…" : "Merge"}
            </button>
          )}
        </>
      }
    >
      {result ? (
        <MergeDone result={result} loserName={loser.variant_name} />
      ) : (
        <div className="flex flex-col gap-4 text-sm">
          <label className="flex flex-col gap-1">
            <span>Survivor — the variant that keeps everything</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={targetId ?? ""}
              onChange={(e) => setTargetId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">Choose…</option>
              {siblings.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.variant_name}
                </option>
              ))}
            </select>
          </label>

          {preview.isPending && targetId != null && <p className="text-slate-500">Checking what would move…</p>}
          <ErrorBanner error={preview.error} />

          {plan && (
            <>
              {plan.blockers.map((b) => (
                <p key={b} className="rounded bg-red-50 p-2 text-red-700">
                  {b}
                </p>
              ))}
              <PlanSummary plan={plan} />
              {plan.bom_differs && (
                <BomChoice
                  title="Bill of materials"
                  plan={plan}
                  loserLines={plan.loser_bom}
                  survivorLines={plan.survivor_bom}
                  value={bom}
                  onChange={setBom}
                />
              )}
              {plan.kitting_differs && (
                <BomChoice
                  title="Kitting BOM"
                  plan={plan}
                  loserLines={plan.loser_kitting}
                  survivorLines={plan.survivor_kitting}
                  value={kitting}
                  onChange={setKitting}
                />
              )}
              <p className="text-xs text-slate-500">
                "{plan.loser.variant_name}" will be disabled, not deleted — its orders, builds and stock history stay
                as they are. Its SKU
                {plan.loser.full_sku ? ` (${plan.loser.full_sku})` : ""} is remembered so future orders quoting it
                land on "{plan.survivor.variant_name}". This can't be undone.
              </p>
            </>
          )}

          {!liveConflict && <ErrorBanner error={mergeMutation.error} />}
          {liveConflict && (
            <LiveListingConflictDialog
              detail={liveConflict}
              busy={mergeMutation.isPending}
              onResolve={(resolution) => mergeMutation.mutate(resolution)}
              onCancel={() => mergeMutation.reset()}
            />
          )}
        </div>
      )}
    </Modal>
  );
}

function PlanSummary({ plan }: { plan: VariantMergePlan }) {
  const openUnits = plan.open_lines.reduce((sum, l) => sum + l.qty, 0);
  return (
    <ul className="flex flex-col gap-1 rounded border border-slate-200 bg-slate-50 p-2">
      <li>
        <strong>{plan.stock_to_move}</strong> in stock will move to "{plan.survivor.variant_name}" (it has{" "}
        {plan.survivor.current_stock}).
      </li>
      <li>
        {plan.open_lines.length === 0
          ? "No open orders are waiting on it."
          : `${openUnits} unit${openUnits === 1 ? "" : "s"} on ${plan.open_lines.length} open order${
              plan.open_lines.length === 1 ? "" : "s"
            } will be moved to the survivor.`}
      </li>
      {plan.live_listings.length > 0 && (
        <li className="text-amber-800">
          Live on {plan.live_listings.map((l) => l.platform).join(", ")} — you'll be asked to confirm setting
          that variation to 0.
        </li>
      )}
      {!plan.bom_differs && !plan.kitting_differs && <li>Both variants use the same materials.</li>}
    </ul>
  );
}

function BomChoice({
  title,
  plan,
  loserLines,
  survivorLines,
  value,
  onChange,
}: {
  title: string;
  plan: VariantMergePlan;
  loserLines: MergeBomLine[];
  survivorLines: MergeBomLine[];
  value: MergeBomChoice;
  onChange: (next: MergeBomChoice) => void;
}) {
  const name = `merge-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <fieldset className="flex flex-col gap-2 rounded border border-amber-200 bg-amber-50 p-2">
      <legend className="px-1 text-xs font-medium text-amber-900">{title} differs — which one should the survivor keep?</legend>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 rounded border border-slate-200 bg-white p-2">
          <span className="flex items-center gap-1 font-medium">
            <input
              type="radio"
              name={name}
              checked={value === "keep_survivor"}
              onChange={() => onChange("keep_survivor")}
            />
            Keep "{plan.survivor.variant_name}"'s
          </span>
          <BomList lines={survivorLines} />
        </label>
        <label className="flex flex-col gap-1 rounded border border-slate-200 bg-white p-2">
          <span className="flex items-center gap-1 font-medium">
            <input type="radio" name={name} checked={value === "take_loser"} onChange={() => onChange("take_loser")} />
            Take "{plan.loser.variant_name}"'s
          </span>
          <BomList lines={loserLines} />
        </label>
      </div>
    </fieldset>
  );
}

function BomList({ lines }: { lines: MergeBomLine[] }) {
  if (lines.length === 0) return <p className="text-xs text-slate-500">No materials.</p>;
  return (
    <ul className="text-xs text-slate-700">
      {lines.map((l) => (
        <li key={`${l.material_id}-${l.replaces_material_id ?? ""}`}>
          {l.material_name} × {l.qty_required}
          {l.replaces_material_name && <span className="text-slate-500"> (for {l.replaces_material_name})</span>}
        </li>
      ))}
    </ul>
  );
}

function MergeDone({ result, loserName }: { result: VariantMergeResult; loserName: string }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        Merged "{loserName}" into "{result.survivor.variant_name}": {result.stock_moved} in stock moved,{" "}
        {result.open_lines_moved} open order line{result.open_lines_moved === 1 ? "" : "s"} moved.
      </p>
      {result.warnings.map((w) => (
        <p key={w} className="rounded bg-amber-50 p-2 text-amber-900">
          {w}
        </p>
      ))}
    </div>
  );
}
