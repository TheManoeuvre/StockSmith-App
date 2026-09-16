import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { platformLimitsApi } from "../../../api/platformLimits";
import { platformsApi, type PlatformStatus } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { PLATFORM_LABELS } from "../../../lib/platforms";
import { ErrorBanner } from "../../common/ErrorBanner";
import { EtsyListingPickerModal } from "../../products/EtsyListingPickerModal";
import { ListingPickerModal } from "../../products/ListingPickerModal";
import { Disclosure } from "../Disclosure";
import { EtsyBackfillPanel } from "../EtsyBackfillPanel";
import { PlatformCompatibilityPanel } from "../PlatformCompatibilityPanel";
import { SettingsCard } from "../SettingsCard";

/**
 * The tools for bringing what already exists on the marketplace into line with StockSmith.
 * They stay available for good rather than hiding once a shop is migrated — every new
 * product or variation is another chance for a listing to be created out of band.
 */
export function StoreToolsCard({
  platform,
  status,
}: {
  platform: ListingPlatform;
  status: PlatformStatus | undefined;
}) {
  const label = PLATFORM_LABELS[platform];

  // Same key as the panel inside the row, so this is the one request, deduplicated — it only
  // exists to put the verdict in the row summary while the row is closed.
  const { data: compatibility } = useQuery({
    queryKey: ["platforms", platform, "catalogue-compatibility"],
    queryFn: () => platformLimitsApi.catalogueCompatibility(platform),
  });
  const compatibilityProblems = compatibility ? compatibility.blocked_count + compatibility.warning_count : 0;

  return (
    <SettingsCard
      title="Tools"
      help={`For listings and products that already exist on ${label}. Run them again whenever new products or variations appear.`}
    >
      <div className="-mx-4 -mb-4 border-t border-slate-100">
        <Disclosure
          title="Compatibility report"
          summary={
            !compatibility
              ? "checking…"
              : compatibilityProblems === 0
                ? `every product fits ${label}'s limits`
                : `${compatibilityProblems} of ${compatibility.total_products} don't fit${compatibility.blocked_count > 0 ? ` · ${compatibility.blocked_count} blocked` : ""}`
          }
          tone={compatibility?.blocked_count ? "danger" : compatibilityProblems > 0 ? "warning" : "normal"}
          // A report that finally has something to say should be seen the day it says it.
          defaultOpen={compatibilityProblems > 0}
        >
          {compatibilityProblems === 0 ? (
            <p className="text-sm text-slate-600">
              Nothing to fix. Checked against the listing limits above.
            </p>
          ) : (
            <PlatformCompatibilityPanel platform={platform} />
          )}
        </Disclosure>
        {platform === "etsy" && (
          <Disclosure
            title="Backfill from Etsy"
            summary="copy descriptions, prices and hero images from linked listings"
          >
            <EtsyBackfillPanel />
          </Disclosure>
        )}
        <UnlinkedListingsRow platform={platform} status={status} />
      </div>
    </SettingsCard>
  );
}

// Deliberately a mutation (an explicit button) rather than a query that runs on load.
// eBay's Trading API budget is 5,000 calls/DAY across all Trading calls combined, and
// one shop-wide scan costs up to _MAX_TRADING_PAGES of them — so auto-fetching this
// every time Settings is opened could burn a meaningful slice of the day's budget on
// a page the user only wanted for something else.
function UnlinkedListingsRow({
  platform,
  status,
}: {
  platform: ListingPlatform;
  status: PlatformStatus | undefined;
}) {
  const label = PLATFORM_LABELS[platform];
  const [showPicker, setShowPicker] = useState(false);
  const available = (status?.connected ?? false) && !(status?.needs_reconnect ?? false);

  const scanMutation = useMutation({
    mutationFn: () =>
      platform === "ebay"
        ? platformsApi
            .fetchUnmigratedListings()
            .then((r) => ({ count: r.total_count, eligible: r.eligible_count }))
        : platformsApi
            .fetchEtsyUnadoptedListings()
            .then((r) => ({ count: r.total_count, eligible: r.total_count })),
  });
  const report = scanMutation.data;

  return (
    <Disclosure
      title="Unlinked listings"
      summary={
        report === undefined
          ? platform === "ebay"
            ? "live listings the Inventory API can't see yet"
            : "live listings whose SKU StockSmith doesn't know"
          : report.count === 0
            ? `every live ${label} listing is linked`
            : `${report.count} unlinked`
      }
      tone={report && report.count > 0 ? "warning" : "normal"}
    >
      <div className="flex flex-col gap-2 text-sm">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-slate-500">
            {platform === "ebay"
              ? "Finds live listings the Inventory API can't see yet, so StockSmith can adopt them and push stock to them."
              : "Finds live listings carrying a SKU StockSmith doesn't know, so they can be linked to a product."}
          </p>
          <button
            type="button"
            onClick={() => scanMutation.mutate()}
            disabled={!available || scanMutation.isPending}
            className="h-7 shrink-0 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium disabled:opacity-50"
          >
            {scanMutation.isPending ? "Scanning…" : `Scan ${label}`}
          </button>
        </div>
        <ErrorBanner error={scanMutation.error} />
        {report !== undefined && report.count === 0 && (
          <p className="rounded bg-green-50 p-2 text-green-800">
            Every live {label} listing is linked to a StockSmith SKU.
          </p>
        )}
        {report !== undefined && report.count > 0 && (
          <div className="flex items-center justify-between rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
            <span>
              {platform === "ebay" ? (
                <>
                  <strong>{report.count}</strong> eBay{" "}
                  {report.count === 1 ? "listing isn't" : "listings aren't"} visible to the Inventory API yet
                  ({report.eligible} look eligible to migrate) — StockSmith can't sync stock to them until
                  they are.
                </>
              ) : (
                <>
                  <strong>{report.count}</strong> Etsy {report.count === 1 ? "listing has" : "listings have"}{" "}
                  no matching StockSmith SKU — stock changes here never reach them.
                </>
              )}
            </span>
            <button
              type="button"
              onClick={() => setShowPicker(true)}
              className="ml-3 shrink-0 rounded-md border border-amber-400 bg-white px-2.5 py-1 text-xs font-medium"
            >
              Review
            </button>
          </div>
        )}
        {showPicker &&
          (platform === "ebay" ? (
            <ListingPickerModal onClose={() => setShowPicker(false)} />
          ) : (
            <EtsyListingPickerModal onClose={() => setShowPicker(false)} />
          ))}
      </div>
    </Disclosure>
  );
}
