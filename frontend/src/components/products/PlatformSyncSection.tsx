import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type UnitSyncResult } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { ApiError } from "../../api/client";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { EtsyListingPickerModal } from "./EtsyListingPickerModal";
import { ListingPickerModal } from "./ListingPickerModal";
import { PlatformSyncBadge } from "./PlatformSyncBadge";

const INITIAL_UNIT_LIMIT = 5;

export function PlatformSyncSection({ productId, platform }: { productId: number; platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [showAllUnits, setShowAllUnits] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const { data, error } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "sync-status"],
    queryFn: () => platformsApi.getProductSyncStatus(platform, productId),
  });

  const checkMutation = useMutation({
    mutationFn: () => platformsApi.checkProductSync(platform, productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products", productId, "sync-status"] });
    },
  });

  const pushCorrectionsMutation = useMutation({
    mutationFn: () => platformsApi.pushCorrections(platform, productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products", productId, "sync-status"] });
    },
  });

  const notConnected = error instanceof ApiError && error.status === 400;
  const mismatchedUnits = data?.units.filter((u) => u.quantity_mismatch) ?? [];
  // Both platforms offer a picker when the SKU check comes up short, but for different
  // reasons: eBay's listing may exist and simply not be migrated yet, while Etsy's
  // listing is visible but carries a SKU StockSmith doesn't know.
  const unresolved = data && (data.product_status === "not_found" || data.product_status === "partial");
  const canFindUnmigrated = platform === "ebay" && unresolved;
  const canFindUnadopted = platform === "etsy" && unresolved;

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="font-medium">{label} Sync</p>
          {data && <PlatformSyncBadge platform={platform} status={data.product_status} />}
        </div>
        <div className="flex items-center gap-2">
          {(canFindUnmigrated || canFindUnadopted) && (
            <button
              onClick={() => setShowPicker(true)}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
            >
              {canFindUnmigrated ? "Find unmigrated listing" : "Find unlinked listing"}
            </button>
          )}
          <button
            onClick={() => checkMutation.mutate()}
            disabled={notConnected}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {checkMutation.isPending ? "Testing…" : `Test ${label} Sync`}
          </button>
        </div>
      </div>
      {showPicker &&
        (platform === "ebay" ? (
          <ListingPickerModal
            productId={productId}
            onClose={() => {
              setShowPicker(false);
              queryClient.invalidateQueries({
                queryKey: ["platforms", platform, "products", productId, "sync-status"],
              });
            }}
          />
        ) : (
          <EtsyListingPickerModal
            productId={productId}
            onClose={() => {
              setShowPicker(false);
              queryClient.invalidateQueries({
                queryKey: ["platforms", platform, "products", productId, "sync-status"],
              });
            }}
          />
        ))}
      {notConnected && <p className="text-sm text-slate-500">Connect {label} in Settings to test SKU sync.</p>}
      <ErrorBanner error={checkMutation.error} />
      <ErrorBanner error={pushCorrectionsMutation.error} />
      {mismatchedUnits.length > 0 && (
        // Testing sync deliberately doesn't correct anything on its own — pushing writes
        // to a live marketplace, so it stays behind an explicit click.
        <div className="flex items-center justify-between gap-3 rounded border border-amber-300 bg-amber-50 p-2 text-sm">
          <span>
            <strong>{mismatchedUnits.length}</strong> unit(s) show a different quantity on {label} than
            StockSmith expects. Pushing will set {label} to StockSmith's numbers.
          </span>
          <button
            onClick={() => pushCorrectionsMutation.mutate()}
            disabled={pushCorrectionsMutation.isPending}
            className="shrink-0 rounded border border-amber-400 bg-white px-3 py-1.5 disabled:opacity-50"
          >
            {pushCorrectionsMutation.isPending ? "Pushing…" : "Push corrections"}
          </button>
        </div>
      )}
      {pushCorrectionsMutation.data && (
        <div className="rounded bg-slate-50 p-2 text-sm">
          Pushed <strong>{pushCorrectionsMutation.data.pushed_count}</strong> correction(s)
          {pushCorrectionsMutation.data.failed_count > 0 && (
            <>
              , <strong className="text-red-700">{pushCorrectionsMutation.data.failed_count} failed</strong>
              <ul className="mt-1 list-disc pl-5 text-xs text-red-700">
                {pushCorrectionsMutation.data.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {data && data.units.length > 0 && (
        <>
          <table className="w-full table-fixed border-collapse text-left text-sm">
            {/* Fixed widths (shared across the Etsy and eBay instances of this component) so the
                two tables' columns line up vertically when stacked, regardless of how long either
                platform's listing title happens to be. */}
            <colgroup>
              <col className="w-[13%]" />
              <col className="w-[15%]" />
              <col className="w-[12%]" />
              <col className="w-[24%]" />
              <col className="w-[12%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
            </colgroup>
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                <th className="p-1">Unit</th>
                <th className="p-1">SKU</th>
                <th className="p-1">Status</th>
                <th className="p-1">{label} listing</th>
                <th className="p-1">{label} variation</th>
                <th className="p-1">{label} status</th>
                <th className="p-1">{label} qty</th>
                <th className="p-1" title="What StockSmith would push for this unit">
                  Expected
                </th>
              </tr>
            </thead>
            <tbody>
              {(showAllUnits ? data.units : data.units.slice(0, INITIAL_UNIT_LIMIT)).map((unit) => (
                <UnitSyncRow key={unit.variant_id ?? "product"} unit={unit} platform={platform} />
              ))}
            </tbody>
          </table>
          {data.units.length > INITIAL_UNIT_LIMIT && (
            <button
              onClick={() => setShowAllUnits((v) => !v)}
              className="self-start text-sm text-slate-600 underline"
            >
              {showAllUnits ? "Show less" : `Show all ${data.units.length} (${data.units.length - INITIAL_UNIT_LIMIT} more)`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function UnitSyncRow({ unit, platform }: { unit: UnitSyncResult; platform: ListingPlatform }) {
  const mismatch = unit.quantity_mismatch;
  return (
    <tr className={`border-b border-slate-100 ${mismatch ? "bg-amber-50" : ""}`}>
      <td className="truncate p-1">{unit.variant_name ?? "(product)"}</td>
      <td className="truncate p-1 font-mono text-xs">{unit.sku ?? "—"}</td>
      <td className="p-1">
        <PlatformSyncBadge platform={platform} status={unit.status} />
      </td>
      <td className="truncate p-1" title={unit.external_title ?? undefined}>
        {unit.external_title ?? "—"}
      </td>
      <td className="truncate p-1" title={unit.external_variation ?? undefined}>
        {unit.external_variation ?? "—"}
      </td>
      <td className="truncate p-1">{unit.external_state ?? "—"}</td>
      {/* Both quantities carry the emphasis on a mismatch, not just one — the point is
          the difference between them, and colouring a single cell reads as "this number
          is wrong" rather than "these two disagree". */}
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : ""}`}>
        {unit.external_quantity ?? "—"}
      </td>
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : "text-slate-500"}`}>
        {unit.expected_quantity ?? "—"}
      </td>
    </tr>
  );
}
