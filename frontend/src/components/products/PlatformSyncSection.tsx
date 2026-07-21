import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type UnitSyncResult } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { ApiError } from "../../api/client";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { PlatformSyncBadge } from "./PlatformSyncBadge";

const INITIAL_UNIT_LIMIT = 5;

export function PlatformSyncSection({ productId, platform }: { productId: number; platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [showAllUnits, setShowAllUnits] = useState(false);
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

  const notConnected = error instanceof ApiError && error.status === 400;

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="font-medium">{label} Sync</p>
          {data && <PlatformSyncBadge platform={platform} status={data.product_status} />}
        </div>
        <button
          onClick={() => checkMutation.mutate()}
          disabled={notConnected}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
        >
          {checkMutation.isPending ? "Testing…" : `Test ${label} Sync`}
        </button>
      </div>
      {notConnected && <p className="text-sm text-slate-500">Connect {label} in Settings to test SKU sync.</p>}
      <ErrorBanner error={checkMutation.error} />
      {data && data.units.length > 0 && (
        <>
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                <th className="p-1">Unit</th>
                <th className="p-1">SKU</th>
                <th className="p-1">Status</th>
                <th className="p-1">{label} listing</th>
                <th className="p-1">{label} variation</th>
                <th className="p-1">{label} status</th>
                <th className="p-1">{label} qty</th>
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
  return (
    <tr className="border-b border-slate-100">
      <td className="p-1">{unit.variant_name ?? "(product)"}</td>
      <td className="p-1 font-mono text-xs">{unit.sku ?? "—"}</td>
      <td className="p-1">
        <PlatformSyncBadge platform={platform} status={unit.status} />
      </td>
      <td className="p-1">{unit.external_title ?? "—"}</td>
      <td className="p-1">{unit.external_variation ?? "—"}</td>
      <td className="p-1">{unit.external_state ?? "—"}</td>
      <td className="p-1">{unit.external_quantity ?? "—"}</td>
    </tr>
  );
}
