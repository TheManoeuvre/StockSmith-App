import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { platformsApi, type AdditionalListingResult, type UnitSyncResult } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { ApiError } from "../../api/client";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { listingProfilesApi } from "../../api/listingProfiles";
import { DraftListingModal } from "./DraftListingModal";
import { EtsyListingPickerModal } from "./EtsyListingPickerModal";
import { ListingPickerModal } from "./ListingPickerModal";
import { PlatformSyncBadge } from "./PlatformSyncBadge";

const INITIAL_UNIT_LIMIT = 5;

export function PlatformSyncSection({ productId, platform }: { productId: number; platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [showAllUnits, setShowAllUnits] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [showDraft, setShowDraft] = useState(false);
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

  // Label / make-primary: local bookkeeping, so the response (the refreshed status) is
  // written straight into the cache rather than refetched.
  const updateListingMutation = useMutation({
    mutationFn: ({ listingId, body }: { listingId: number; body: { role?: string | null; is_primary?: true } }) =>
      platformsApi.updateListing(platform, listingId, body),
    onSuccess: (summary) => {
      queryClient.setQueryData(["platforms", platform, "products", productId, "sync-status"], summary);
    },
  });

  // Local-only, so it costs nothing to know whether a draft is possible before offering it
  // — the button is never shown in a state where pressing it would fail.
  const { data: readiness } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "draft-readiness"],
    queryFn: () => listingProfilesApi.draftReadiness(platform, productId),
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
          {/* Offered on the same unresolved state as the pickers, but for the opposite
              case: those adopt a listing that already exists, this creates one that
              doesn't. Etsy only for now — eBay's draft path is a later stage. */}
          {unresolved && platform === "etsy" && (
            <button
              onClick={() => setShowDraft(true)}
              disabled={!readiness?.can_create}
              title={
                readiness?.can_create
                  ? undefined
                  : "Something is still missing: open the listing setup above to see what."
              }
              className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              Create {label} draft
            </button>
          )}
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
      {showDraft && (
        <DraftListingModal
          productId={productId}
          platform={platform}
          onClose={() => {
            setShowDraft(false);
            queryClient.invalidateQueries({
              queryKey: ["platforms", platform, "products", productId, "sync-status"],
            });
          }}
        />
      )}
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
      <ErrorBanner error={updateListingMutation.error} />
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
              <col className="w-[11%]" />
              <col className="w-[13%]" />
              <col className="w-[10%]" />
              <col className="w-[21%]" />
              <col className="w-[11%]" />
              <col className="w-[7%]" />
              <col className="w-[7%]" />
              <col className="w-[8%]" />
              <col className="w-[12%]" />
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
                <th className="p-1" title="Name listings that share a SKU, or choose which one is primary">
                  Label
                </th>
              </tr>
            </thead>
            <tbody>
              {(showAllUnits ? data.units : data.units.slice(0, INITIAL_UNIT_LIMIT)).map((unit) => (
                <UnitSyncRows
                  key={unit.variant_id ?? "product"}
                  unit={unit}
                  platform={platform}
                  onUpdate={(listingId, body) => updateListingMutation.mutate({ listingId, body })}
                  busy={updateListingMutation.isPending}
                />
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

type ListingUpdateBody = { role?: string | null; is_primary?: true };

// A unit's primary row, then one indented row per further listing that holds its SKU. The
// label column only has content when there is something to tell apart, so a unit with a
// single listing looks exactly as it always did.
function UnitSyncRows({
  unit,
  platform,
  onUpdate,
  busy,
}: {
  unit: UnitSyncResult;
  platform: ListingPlatform;
  onUpdate: (listingId: number, body: ListingUpdateBody) => void;
  busy: boolean;
}) {
  const shared = unit.additional_listings.length > 0;
  return (
    <Fragment>
      <UnitSyncRow
        unit={unit}
        platform={platform}
        action={
          shared && unit.listing_id !== null ? (
            <ListingLabel
              role={unit.role}
              placeholder="Primary"
              disabled={busy}
              onSave={(role) => onUpdate(unit.listing_id as number, { role })}
            />
          ) : null
        }
      />
      {unit.additional_listings.map((extra) => (
        <AdditionalListingRow
          key={extra.listing_id}
          extra={extra}
          expected={unit.expected_quantity}
          platform={platform}
          busy={busy}
          onUpdate={onUpdate}
        />
      ))}
    </Fragment>
  );
}

function UnitSyncRow({
  unit,
  platform,
  action,
}: {
  unit: UnitSyncResult;
  platform: ListingPlatform;
  action: ReactNode;
}) {
  const mismatch = unit.quantity_mismatch;
  return (
    <tr className={`border-b border-slate-100 ${mismatch ? "bg-amber-50" : ""}`}>
      <td className="truncate p-1">{unit.variant_name ?? "(product)"}</td>
      <td className="truncate p-1 font-mono text-xs">{unit.sku ?? "-"}</td>
      <td className="p-1">
        <PlatformSyncBadge platform={platform} status={unit.status} />
      </td>
      <td className="truncate p-1" title={unit.external_title ?? undefined}>
        {unit.external_title ?? "-"}
      </td>
      <td className="truncate p-1" title={unit.external_variation ?? undefined}>
        {unit.external_variation ?? "-"}
      </td>
      <td className="truncate p-1">{unit.external_state ?? "-"}</td>
      {/* Both quantities carry the emphasis on a mismatch, not just one — the point is
          the difference between them, and colouring a single cell reads as "this number
          is wrong" rather than "these two disagree". */}
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : ""}`}>
        {unit.external_quantity ?? "-"}
      </td>
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : "text-slate-500"}`}>
        {unit.expected_quantity ?? "-"}
      </td>
      <td className="p-1">{action}</td>
    </tr>
  );
}

function AdditionalListingRow({
  extra,
  expected,
  platform,
  busy,
  onUpdate,
}: {
  extra: AdditionalListingResult;
  expected: number | null;
  platform: ListingPlatform;
  busy: boolean;
  onUpdate: (listingId: number, body: ListingUpdateBody) => void;
}) {
  const mismatch = extra.quantity_mismatch;
  return (
    <tr className={`border-b border-slate-100 ${mismatch ? "bg-amber-50" : "bg-slate-50"}`}>
      <td className="truncate p-1 pl-4 text-slate-500" title="Another listing with the same SKU">
        ↳ also listed
      </td>
      <td className="p-1" />
      <td className="p-1">
        <PlatformSyncBadge platform={platform} status={extra.status} />
      </td>
      <td className="truncate p-1" title={extra.external_title ?? undefined}>
        {extra.external_title ?? "-"}
      </td>
      <td className="truncate p-1" title={extra.external_variation ?? undefined}>
        {extra.external_variation ?? "-"}
      </td>
      <td className="truncate p-1">{extra.external_state ?? "-"}</td>
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : ""}`}>
        {extra.external_quantity ?? "-"}
      </td>
      <td className={`truncate p-1 ${mismatch ? "font-medium text-amber-800" : "text-slate-500"}`}>
        {expected ?? "-"}
      </td>
      <td className="p-1">
        <div className="flex flex-col gap-1">
          <ListingLabel
            role={extra.role}
            placeholder="Add label"
            disabled={busy}
            onSave={(role) => onUpdate(extra.listing_id, { role })}
          />
          <button
            onClick={() => onUpdate(extra.listing_id, { is_primary: true })}
            disabled={busy}
            title="The primary listing is the one this unit's status and badge describe. Stock is pushed to every listing either way."
            className="self-start text-xs text-slate-600 underline disabled:opacity-50"
          >
            Make primary
          </button>
        </div>
      </td>
    </tr>
  );
}

// Saves on blur or Enter, and only when the text actually changed, so tabbing through the
// table doesn't write anything.
function ListingLabel({
  role,
  placeholder,
  disabled,
  onSave,
}: {
  role: string | null;
  placeholder: string;
  disabled: boolean;
  onSave: (role: string | null) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? role ?? "";
  const commit = () => {
    if (draft !== null && draft.trim() !== (role ?? "")) onSave(draft.trim() || null);
    setDraft(null);
  };
  return (
    <input
      aria-label="Listing label"
      value={value}
      maxLength={60}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setDraft(null);
      }}
      className="w-full rounded border border-slate-200 px-1 py-0.5 text-xs"
    />
  );
}
