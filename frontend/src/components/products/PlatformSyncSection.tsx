import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { platformsApi, type UnitSyncResult } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { ApiError } from "../../api/client";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { Badge } from "../common/Badge";
import { ErrorBanner } from "../common/ErrorBanner";
import { Th } from "../common/ListTable";
import { listingProfilesApi } from "../../api/listingProfiles";
import { Disclosure } from "../settings/Disclosure";
import { DraftListingModal } from "./DraftListingModal";
import { EtsyListingPickerModal } from "./EtsyListingPickerModal";
import { ListingPickerModal } from "./ListingPickerModal";
import { PlatformSyncBadge } from "./PlatformSyncBadge";

const INITIAL_UNIT_LIMIT = 5;

type ListingUpdateBody = { role?: string | null; is_primary?: true };
type OnUpdate = (listingId: number, body: ListingUpdateBody) => void;

/**
 * One marketplace listing's line in the sync card. A unit usually has exactly one; a unit whose
 * SKU is held by several listings has its primary first, then one row per further listing.
 */
interface ListingRow {
  key: string;
  kind: "primary" | "extra";
  unitName: string;
  sku: string | null;
  listingId: number | null;
  status: UnitSyncResult["status"];
  variation: string | null;
  title: string | null;
  state: string | null;
  role: string | null;
  quantity: number | null;
  expected: number | null;
  drifted: boolean;
  /** The unit has further listings on this SKU, so labels and "make primary" apply. */
  shared: boolean;
}

function listingRows(unit: UnitSyncResult): ListingRow[] {
  const shared = unit.additional_listings.length > 0;
  const unitName = unit.variant_name ?? "(product)";
  const primary: ListingRow = {
    key: `${unit.variant_id ?? "product"}-primary`,
    kind: "primary",
    unitName,
    sku: unit.sku,
    listingId: unit.listing_id,
    status: unit.status,
    variation: unit.external_variation,
    title: unit.external_title,
    state: unit.external_state,
    role: unit.role,
    quantity: unit.external_quantity,
    expected: unit.expected_quantity,
    // The unit flag also covers its further listings, so the primary is compared directly.
    drifted:
      unit.external_quantity !== null &&
      unit.expected_quantity !== null &&
      unit.external_quantity !== unit.expected_quantity,
    shared,
  };
  const extras = unit.additional_listings.map<ListingRow>((extra) => ({
    key: `${unit.variant_id ?? "product"}-${extra.listing_id}`,
    kind: "extra",
    unitName,
    sku: unit.sku,
    listingId: extra.listing_id,
    status: extra.status,
    variation: extra.external_variation,
    title: extra.external_title,
    state: extra.external_state,
    role: extra.role,
    quantity: extra.external_quantity,
    expected: unit.expected_quantity,
    drifted: extra.quantity_mismatch,
    shared: true,
  }));
  return [primary, ...extras];
}

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
    mutationFn: ({ listingId, body }: { listingId: number; body: ListingUpdateBody }) =>
      platformsApi.updateListing(platform, listingId, body),
    onSuccess: (summary) => {
      queryClient.setQueryData(["platforms", platform, "products", productId, "sync-status"], summary);
    },
  });
  const onUpdate: OnUpdate = (listingId, body) => updateListingMutation.mutate({ listingId, body });
  const busy = updateListingMutation.isPending;

  // Local-only, so it costs nothing to know whether a draft is possible before offering it
  // — the button is never shown in a state where pressing it would fail.
  const { data: readiness } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "draft-readiness"],
    queryFn: () => listingProfilesApi.draftReadiness(platform, productId),
  });

  const notConnected = error instanceof ApiError && error.status === 400;
  const units = data?.units ?? [];
  // What needs attention is split from what doesn't, so a long list of matching units stays out
  // of the way and the one that differs is the only thing in the card that is loud.
  const diffs = units.filter((u) => u.quantity_mismatch);
  const matching = units.filter((u) => !u.quantity_mismatch);
  // Both platforms offer a picker when the SKU check comes up short, but for different
  // reasons: eBay's listing may exist and simply not be migrated yet, while Etsy's
  // listing is visible but carries a SKU StockSmith doesn't know.
  const unresolved = data && (data.product_status === "not_found" || data.product_status === "partial");
  const canFindUnmigrated = platform === "ebay" && unresolved;
  const canFindUnadopted = platform === "etsy" && unresolved;

  // The listing is normally the same for every unit (they're variations of one listing), so it is
  // said once in the header. A unit whose listing differs says so on its own row instead.
  const headline = units.find((u) => u.external_listing_id !== null);
  const sameListing = (u: UnitSyncResult) =>
    u.external_listing_id === headline?.external_listing_id &&
    u.external_title === headline?.external_title &&
    u.external_state === headline?.external_state;
  const listingIsShared = headline !== undefined && units.every(sameListing);

  const problemCount = matching.filter((u) => u.status !== "synced" && u.status !== "not_tested").length;
  const untested = matching.some((u) => u.status === "not_tested");
  const needsOpen = matching.some((u) => u.additional_listings.length > 0);

  const buttonClass = "shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50";

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-3 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[13px] font-semibold">{label} sync</p>
            {data && <PlatformSyncBadge platform={platform} status={data.product_status} />}
            {diffs.length > 0 ? (
              <Badge className="bg-amber-100 text-amber-800">
                {diffs.length} {diffs.length === 1 ? "unit differs" : "units differ"}
              </Badge>
            ) : (
              units.length > 0 &&
              units.every((u) => u.status === "synced") && (
                <Badge className="bg-emerald-100 text-emerald-800">All {units.length} match</Badge>
              )
            )}
          </div>
          {listingIsShared && headline && (
            // Etsy titles can run to 140 characters, so this stays on one line; the full text is the tooltip.
            <p
              className="mt-1 truncate text-[11px] text-slate-500"
              title={[headline.external_listing_id, headline.external_title, headline.external_state].filter(Boolean).join(" · ")}
            >
              Listing <span className="font-mono">{headline.external_listing_id}</span>
              {headline.external_title && (
                <>
                  {" · "}
                  <span>{headline.external_title}</span>
                </>
              )}
              {headline.external_state && (
                <>
                  {" · "}
                  <span>{headline.external_state}</span>
                </>
              )}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
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
              className={buttonClass}
            >
              Create {label} draft
            </button>
          )}
          {(canFindUnmigrated || canFindUnadopted) && (
            <button onClick={() => setShowPicker(true)} className={buttonClass}>
              {canFindUnmigrated ? "Find unmigrated listing" : "Find unlinked listing"}
            </button>
          )}
          <button onClick={() => checkMutation.mutate()} disabled={notConnected} className={buttonClass}>
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
      {notConnected && <p className="px-3 pb-3 text-sm text-slate-500">Connect {label} in Settings to test SKU sync.</p>}
      <div className="flex flex-col gap-2 px-3 empty:hidden [&:not(:empty)]:pb-3">
        <ErrorBanner error={checkMutation.error} />
        <ErrorBanner error={pushCorrectionsMutation.error} />
        <ErrorBanner error={updateListingMutation.error} />
      </div>
      {diffs.length > 0 && (
        // Testing sync deliberately doesn't correct anything on its own — pushing writes
        // to a live marketplace, so it stays behind an explicit click. The button sits
        // inside the warning it resolves.
        <div className="mx-3 mb-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <div className="flex items-start justify-between gap-3">
            <p className="text-[12.5px] text-amber-800">
              <strong>{diffs.length}</strong> unit(s) show a different quantity on {label} than StockSmith
              expects. Pushing will set {label} to StockSmith's numbers.
            </p>
            <button
              onClick={() => pushCorrectionsMutation.mutate()}
              disabled={pushCorrectionsMutation.isPending}
              className="shrink-0 rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {pushCorrectionsMutation.isPending ? "Pushing…" : "Push corrections"}
            </button>
          </div>
          {diffs.flatMap(listingRows).map((row) => (
            <AttentionRow
              key={row.key}
              row={row}
              label={label}
              showListing={!listingIsShared}
              onUpdate={onUpdate}
              busy={busy}
            />
          ))}
        </div>
      )}
      {pushCorrectionsMutation.data && (
        <div className="mx-3 mb-3 rounded-md bg-slate-50 p-2 text-[12.5px]">
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
      {matching.length > 0 && (
        <div className="border-t border-slate-200 px-3">
          {/* A unit sharing its SKU with another listing has labels to manage, so the list opens
              by itself there rather than hiding the controls. */}
          <Disclosure
            title={diffs.length === 0 ? `All ${matching.length} units` : `${matching.length} other ${matching.length === 1 ? "unit" : "units"}`}
            summary={
              untested
                ? `not yet checked: run Test ${label} Sync`
                : problemCount > 0
                  ? `${problemCount} with a listing problem`
                  : `match ${label}: quantity, SKU and listing status`
            }
            tone={problemCount > 0 && !untested ? "warning" : "normal"}
            defaultOpen={needsOpen}
          >
            <MatchingTable
              units={matching}
              platform={platform}
              label={label}
              showListing={!listingIsShared}
              showAll={showAllUnits}
              onToggleAll={() => setShowAllUnits((v) => !v)}
              onUpdate={onUpdate}
              busy={busy}
            />
          </Disclosure>
        </div>
      )}
    </div>
  );
}

// "Etsy 14 → expected 10": the pair is the point, so both numbers carry weight and the Etsy one
// carries the warning colour. A row in a drifted group that itself matches stays neutral.
function QuantityPair({ label, row }: { label: string; row: ListingRow }) {
  return (
    <span className="shrink-0 text-right tabular-nums">
      <span className="text-[11px] text-slate-500">{label}</span>{" "}
      <strong className={`font-semibold ${row.drifted ? "text-amber-800" : ""}`}>{row.quantity ?? "-"}</strong>{" "}
      <span className="text-slate-400">→</span> <span className="text-[11px] text-slate-500">expected</span>{" "}
      <strong className="font-semibold">{row.expected ?? "-"}</strong>
    </span>
  );
}

// What tells this row's listing apart from the others: the label (an input when the SKU is
// shared, plain text when a label was set on a lone listing) and, on an extra listing, the
// action that promotes it.
function ListingLabelCell({
  row,
  onUpdate,
  busy,
}: {
  row: ListingRow;
  onUpdate: OnUpdate;
  busy: boolean;
}): ReactNode {
  if (row.shared && row.listingId !== null) {
    const listingId = row.listingId;
    return (
      <div className="mt-1 flex flex-col gap-1">
        <ListingLabel
          role={row.role}
          placeholder={row.kind === "extra" ? "Add label" : "Primary"}
          disabled={busy}
          onSave={(role) => onUpdate(listingId, { role })}
        />
        {row.kind === "extra" && (
          <button
            onClick={() => onUpdate(listingId, { is_primary: true })}
            disabled={busy}
            title="The primary listing is the one this unit's status and badge describe. Stock is pushed to every listing either way."
            className="self-start text-[11px] text-slate-600 underline disabled:opacity-50"
          >
            Make primary
          </button>
        )}
      </div>
    );
  }
  return row.role ? <p className="text-[11px] text-slate-500">Label: {row.role}</p> : null;
}

function ListingDetail({ row }: { row: ListingRow }) {
  return (
    <p className="text-[11px] text-slate-500">
      {row.title && <span>{row.title}</span>}
      {row.title && row.state && " · "}
      {row.state && <span>{row.state}</span>}
    </p>
  );
}

function AttentionRow({
  row,
  label,
  showListing,
  onUpdate,
  busy,
}: {
  row: ListingRow;
  label: string;
  showListing: boolean;
  onUpdate: OnUpdate;
  busy: boolean;
}) {
  return (
    <div className="mt-2 flex items-center gap-3 rounded-md bg-white px-3 py-2 text-[12.5px]">
      <div className="min-w-0 flex-1">
        <p className="font-medium" title={row.kind === "extra" ? "Another listing with the same SKU" : undefined}>
          {row.kind === "extra" ? "↳ also listed" : row.unitName}
        </p>
        <p className="font-mono text-[11px] text-slate-500">{row.sku ?? "-"}</p>
      </div>
      <div className="min-w-0 flex-1">
        {row.kind === "extra" && row.title && <p className="text-slate-700">{row.title}</p>}
        <p className="text-slate-700">{row.variation ?? "-"}</p>
        {showListing && <ListingDetail row={row} />}
        <ListingLabelCell row={row} onUpdate={onUpdate} busy={busy} />
      </div>
      <QuantityPair label={label} row={row} />
    </div>
  );
}

function MatchingTable({
  units,
  platform,
  label,
  showListing,
  showAll,
  onToggleAll,
  onUpdate,
  busy,
}: {
  units: UnitSyncResult[];
  platform: ListingPlatform;
  label: string;
  showListing: boolean;
  showAll: boolean;
  onToggleAll: () => void;
  onUpdate: OnUpdate;
  busy: boolean;
}) {
  const shown = showAll ? units : units.slice(0, INITIAL_UNIT_LIMIT);
  return (
    <>
      <table className="mb-3 w-full border-collapse text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>Unit</Th>
            <Th>{label} variation</Th>
            <Th align="right">{label} qty</Th>
            <Th align="right">Expected</Th>
          </tr>
        </thead>
        <tbody>
          {shown.flatMap(listingRows).map((row) => (
            <tr key={row.key} className={`border-b border-slate-100 align-top ${row.kind === "extra" ? "bg-slate-50" : ""}`}>
              <td className="p-2">
                {row.kind === "extra" ? (
                  <p className="font-medium text-slate-500" title="Another listing with the same SKU">
                    ↳ also listed
                  </p>
                ) : (
                  <p className="font-medium">{row.unitName}</p>
                )}
                <p className="font-mono text-[11px] text-slate-500">{row.sku ?? "-"}</p>
                {/* Matching rows carry no per-row "synced" badge — the card header already says it.
                    Only a row with something other than that to say gets one. */}
                {row.status !== "synced" && row.status !== "not_tested" && (
                  <div className="mt-1">
                    <PlatformSyncBadge platform={platform} status={row.status} />
                  </div>
                )}
              </td>
              <td className="p-2">
                {row.kind === "extra" && row.title && <p className="text-slate-700">{row.title}</p>}
                <p className="text-slate-700">{row.variation ?? "-"}</p>
                {showListing && <ListingDetail row={row} />}
                <ListingLabelCell row={row} onUpdate={onUpdate} busy={busy} />
              </td>
              <td className={`p-2 text-right tabular-nums ${row.drifted ? "font-medium text-amber-800" : ""}`}>
                {row.quantity ?? "-"}
              </td>
              <td className="p-2 text-right tabular-nums text-slate-500">{row.expected ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {units.length > INITIAL_UNIT_LIMIT && (
        <button onClick={onToggleAll} className="mb-3 text-[12.5px] text-blue-700 hover:underline">
          {showAll ? "Show less" : `Show all ${units.length} (${units.length - INITIAL_UNIT_LIMIT} more)`}
        </button>
      )}
    </>
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
