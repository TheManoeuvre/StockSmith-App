import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { productsApi } from "../../api/products";
import type {
  AttributeValueMergePlan,
  AttributeValueMergeResult,
  LiveListingResolution,
  MergeBomChoice,
  VariantMergePlan,
} from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LiveListingConflictDialog, liveListingConflictDetail } from "./LiveListingConflictDialog";
import { BomList } from "./VariantMergeModal";

/**
 * Merge one attribute value into another: "4 Stud Standard" → "4 Stud".
 *
 * Every variant with the loser value either has a counterpart (same other attributes,
 * survivor value) and is merged into it — stock, open orders, SKU alias, the lot — or has
 * none and is simply relabelled. The preview lists both groups so the user can see which
 * is which before anything moves. One BOM answer covers every pair, so pairs whose
 * recipes differ are shown with both so the answer can be given knowingly.
 */
export function AttributeValueMergeModal({
  productId,
  slot,
  attributeName,
  loserValue,
  candidates,
  initialSurvivor,
  onClose,
}: {
  productId: number;
  slot: 1 | 2 | 3;
  attributeName: string;
  loserValue: string;
  /** The other values of the same attribute. */
  candidates: string[];
  initialSurvivor?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [survivor, setSurvivor] = useState<string>(
    initialSurvivor ?? (candidates.length === 1 ? candidates[0] : ""),
  );
  const [bom, setBom] = useState<MergeBomChoice>("keep_survivor");
  const [kitting, setKitting] = useState<MergeBomChoice>("keep_survivor");
  const [result, setResult] = useState<AttributeValueMergeResult | null>(null);

  const preview = useQuery({
    queryKey: ["products", productId, "attribute-value-merge-preview", slot, loserValue, survivor],
    queryFn: () =>
      productsApi.previewAttributeValueMerge(productId, { slot, loser_value: loserValue, survivor_value: survivor }),
    enabled: survivor !== "" && result == null,
    retry: false,
  });
  const plan = preview.data;

  const mergeMutation = useMutation({
    mutationFn: (onLiveListing: LiveListingResolution) =>
      productsApi.mergeAttributeValue(productId, {
        slot,
        loser_value: loserValue,
        survivor_value: survivor,
        bom,
        kitting,
        on_live_listing: onLiveListing,
      }),
    onSuccess: (merged) => {
      setResult(merged);
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["variants"] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });
  const liveConflict = liveListingConflictDetail(mergeMutation.error);
  const canMerge = plan != null && plan.blockers.length === 0 && !mergeMutation.isPending;

  return (
    <Modal
      title={`Merge ${attributeName} "${loserValue}" into…`}
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
        <div className="flex flex-col gap-2 text-sm">
          <p>
            Merged "{loserValue}" into "{survivor}": {result.pairs_merged} variant
            {result.pairs_merged === 1 ? "" : "s"} merged, {result.relabelled} relabelled, {result.stock_moved} in
            stock and {result.open_lines_moved} open order line{result.open_lines_moved === 1 ? "" : "s"} moved.
          </p>
          {result.warnings.map((w) => (
            <p key={w} className="rounded bg-amber-50 p-2 text-amber-900">
              {w}
            </p>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4 text-sm">
          <label className="flex flex-col gap-1">
            <span>Survivor — the {attributeName} value that stays</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={survivor}
              onChange={(e) => setSurvivor(e.target.value)}
            >
              <option value="">Choose…</option>
              {candidates.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>

          {preview.isPending && survivor !== "" && <p className="text-slate-500">Checking what would move…</p>}
          <ErrorBanner error={preview.error} />

          {plan && (
            <>
              {plan.blockers.map((b) => (
                <p key={b} className="rounded bg-red-50 p-2 text-red-700">
                  {b}
                </p>
              ))}
              <PairTable plan={plan} />
              {plan.bom_differs && (
                <GlobalChoice
                  title="Bill of materials"
                  pairs={plan.pairs.filter((p) => p.bom_differs)}
                  pick={(p) => [p.survivor_bom, p.loser_bom]}
                  value={bom}
                  onChange={setBom}
                />
              )}
              {plan.kitting_differs && (
                <GlobalChoice
                  title="Kitting BOM"
                  pairs={plan.pairs.filter((p) => p.kitting_differs)}
                  pick={(p) => [p.survivor_kitting, p.loser_kitting]}
                  value={kitting}
                  onChange={setKitting}
                />
              )}
              <p className="text-xs text-slate-500">
                Merged variants are disabled, not deleted — their orders, builds and stock history stay as they are,
                and their SKUs are remembered so future orders quoting them land on the survivor. No SKU is changed.
                This can't be undone.
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

function PairTable({ plan }: { plan: AttributeValueMergePlan }) {
  return (
    <div className="flex flex-col gap-2">
      {plan.pairs.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1 pr-2 font-medium">Merge</th>
              <th className="py-1 pr-2 font-medium">into</th>
              <th className="py-1 pr-2 font-medium">Stock</th>
              <th className="py-1 pr-2 font-medium">Open orders</th>
              <th className="py-1 font-medium">Notes</th>
            </tr>
          </thead>
          <tbody>
            {plan.pairs.map((p) => (
              <tr key={p.loser.id} className="border-t border-slate-100">
                <td className="py-1 pr-2">{p.loser.variant_name}</td>
                <td className="py-1 pr-2">{p.survivor.variant_name}</td>
                <td className="py-1 pr-2">
                  {p.stock_to_move} → {p.survivor.current_stock + p.stock_to_move}
                </td>
                <td className="py-1 pr-2">{p.open_lines.length}</td>
                <td className="py-1 text-amber-800">
                  {[
                    p.bom_differs ? "BOM differs" : null,
                    p.kitting_differs ? "kitting differs" : null,
                    p.live_listings.length > 0 ? `live on ${p.live_listings.map((l) => l.platform).join(", ")}` : null,
                  ]
                    .filter(Boolean)
                    .join("; ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {plan.relabel_only.length > 0 && (
        <p className="text-xs text-slate-600">
          No counterpart, so just relabelled: {plan.relabel_only.map((r) => r.variant_name).join(", ")}.
        </p>
      )}
    </div>
  );
}

function GlobalChoice({
  title,
  pairs,
  pick,
  value,
  onChange,
}: {
  title: string;
  pairs: VariantMergePlan[];
  pick: (p: VariantMergePlan) => [VariantMergePlan["survivor_bom"], VariantMergePlan["loser_bom"]];
  value: MergeBomChoice;
  onChange: (next: MergeBomChoice) => void;
}) {
  const name = `value-merge-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <fieldset className="flex flex-col gap-2 rounded border border-amber-200 bg-amber-50 p-2">
      <legend className="px-1 text-xs font-medium text-amber-900">
        {title} differs on {pairs.length} pair{pairs.length === 1 ? "" : "s"} — which side should each survivor keep?
      </legend>
      <div className="flex gap-4">
        <label className="flex items-center gap-1 font-medium">
          <input type="radio" name={name} checked={value === "keep_survivor"} onChange={() => onChange("keep_survivor")} />
          Keep the survivor's
        </label>
        <label className="flex items-center gap-1 font-medium">
          <input type="radio" name={name} checked={value === "take_loser"} onChange={() => onChange("take_loser")} />
          Take the merged variant's
        </label>
      </div>
      {pairs.map((p) => {
        const [survivorLines, loserLines] = pick(p);
        return (
          <div key={p.loser.id} className="grid grid-cols-2 gap-2 rounded border border-slate-200 bg-white p-2">
            <div>
              <p className="text-xs font-medium">{p.survivor.variant_name}</p>
              <BomList lines={survivorLines} />
            </div>
            <div>
              <p className="text-xs font-medium">{p.loser.variant_name}</p>
              <BomList lines={loserLines} />
            </div>
          </div>
        );
      })}
    </fieldset>
  );
}
