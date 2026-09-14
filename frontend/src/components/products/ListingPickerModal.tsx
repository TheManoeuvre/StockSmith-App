import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  platformsApi,
  type AdoptListingResult,
  type EligibilityAnnotatedCandidate,
  type VariationMappingChoice,
} from "../../api/platforms";
import { productsApi } from "../../api/products";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";

// eBay-only — a classic (unmigrated) listing is an eBay-specific concept (see
// EbayAdapter.fetch_classic_listings). Etsy's analogous gap is the reverse situation
// (listing visible, StockSmith missing the SKU) and has its own picker.
//
// `productId` omitted = shop-wide mode: the listing list isn't ranked against a product
// and the user must choose which StockSmith product to link to as an extra first step.
export function ListingPickerModal({
  productId,
  onClose,
}: {
  productId?: number;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [chosenProductId, setChosenProductId] = useState<number | null>(
    productId ?? null,
  );
  const [selected, setSelected] =
    useState<EligibilityAnnotatedCandidate | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [alignSkus, setAlignSkus] = useState(true);
  const [confirmed, setConfirmed] = useState(false);

  const { data: products } = useQuery({
    queryKey: ["products", "all"],
    queryFn: () => productsApi.list(),
    enabled: productId === undefined,
  });

  const {
    data: report,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["platforms", "ebay", "unmigrated-listings", productId ?? "all"],
    queryFn: () =>
      productId === undefined
        ? platformsApi.fetchUnmigratedListings()
        : platformsApi.fetchProductUnmigratedListings(productId),
  });

  // Deliberately keyed on the product too: the proposal pairs THIS product's variants
  // against the listing's variations, so reusing a cached one across products would
  // offer a mapping built for the wrong variant set.
  const { data: proposal, isLoading: proposalLoading } = useQuery({
    queryKey: [
      "platforms",
      "ebay",
      "variation-mapping",
      chosenProductId,
      selected?.external_listing_id,
    ],
    queryFn: () =>
      platformsApi.fetchVariationMapping(
        chosenProductId!,
        selected!.external_listing_id,
      ),
    enabled: selected !== null && chosenProductId !== null,
  });

  const proposalEntries = useMemo(() => proposal?.entries ?? [], [proposal]);

  const effectiveMapping = useMemo(() => {
    const result: Record<string, string> = {};
    for (const entry of proposalEntries) {
      const key =
        entry.variant_id === null ? "product" : String(entry.variant_id);
      result[key] = mapping[key] ?? entry.matched_sku ?? "";
    }
    return result;
  }, [proposalEntries, mapping]);

  // The proposal comes back from a GetItem detail call, so its matched_sku values are
  // the authoritative SKU list — the row's own `skus` may be incomplete when
  // detail_loaded is false.
  const selectableSkus = useMemo(() => {
    const fromProposal = proposalEntries
      .map((e) => e.matched_sku)
      .filter((s): s is string => !!s);
    return fromProposal.length > 0
      ? Array.from(new Set(fromProposal))
      : (selected?.skus ?? []);
  }, [proposalEntries, selected]);

  const mappingComplete =
    proposalEntries.length > 0 &&
    proposalEntries.every(
      (e) =>
        effectiveMapping[
          e.variant_id === null ? "product" : String(e.variant_id)
        ],
    );

  const adoptMutation = useMutation({
    mutationFn: () => {
      if (!selected || chosenProductId === null)
        throw new Error("No listing or product selected");
      const variation_mapping: VariationMappingChoice[] = proposalEntries.map(
        (e) => ({
          variant_id: e.variant_id,
          sku: effectiveMapping[
            e.variant_id === null ? "product" : String(e.variant_id)
          ],
        }),
      );
      return platformsApi.adoptEbayListing(chosenProductId, {
        external_listing_id: selected.external_listing_id,
        listing_title: selected.title,
        variation_mapping,
        align_skus: alignSkus,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", "ebay"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const done = adoptMutation.data !== undefined;

  return (
    <Modal
      title="Link an eBay listing"
      subtitle={
        report &&
        !selected && (
          <p className="text-sm text-slate-500">
            {report.eligible_count} of {report.total_count} unmigrated
            listing(s) look eligible to adopt
          </p>
        )
      }
      maxWidth="max-w-2xl"
      onClose={adoptMutation.isPending ? () => {} : onClose}
      footer={
        <>
          {selected && !done && (
            <button
              onClick={() => setSelected(null)}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm"
            >
              Back
            </button>
          )}
          <button
            onClick={onClose}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm"
          >
            {done ? "Close" : "Cancel"}
          </button>
          {selected && !done && (
            <button
              onClick={() => adoptMutation.mutate()}
              disabled={
                adoptMutation.isPending || !mappingComplete || !confirmed
              }
              className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {adoptMutation.isPending
                ? "Migrating & linking…"
                : "Migrate & link"}
            </button>
          )}
        </>
      }
    >
      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      <ErrorBanner error={error} />

      {!selected && (
        <>
          {productId === undefined && (
            <label className="mb-3 flex flex-col gap-1 text-sm">
              <span className="text-slate-500">Link to StockSmith product</span>
              <select
                className="rounded-md border border-slate-300 px-2 py-1.5"
                value={chosenProductId ?? ""}
                onChange={(e) =>
                  setChosenProductId(
                    e.target.value ? Number(e.target.value) : null,
                  )
                }
              >
                <option value="">Select a product…</option>
                {products?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.sku ? ` (${p.sku})` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="flex flex-col gap-2">
            {report?.listings.length === 0 && (
              <p className="text-sm text-slate-600">
                No unmigrated eBay listings found for this shop.
              </p>
            )}
            {report?.listings.map((candidate) => {
              const eligible = candidate.ineligibility_reasons.length === 0;
              const selectable = eligible && chosenProductId !== null;
              return (
                <button
                  key={candidate.external_listing_id}
                  disabled={!selectable}
                  onClick={() => setSelected(candidate)}
                  className={`rounded-md border p-3 text-left text-sm ${
                    selectable
                      ? "border-slate-200 hover:border-slate-400"
                      : "cursor-not-allowed border-slate-100 bg-slate-50 opacity-60"
                  }`}
                >
                  <p className="font-medium">{candidate.title}</p>
                  <p className="text-xs text-slate-500">
                    Item {candidate.external_listing_id} · qty{" "}
                    {candidate.quantity} ·{" "}
                    {candidate.detail_loaded
                      ? `${candidate.skus.length} SKU(s): ${candidate.skus.join(", ") || "none"}`
                      : "SKUs checked when you select it"}
                  </p>
                  {!eligible && (
                    <ul className="mt-1 list-inside list-disc text-xs text-red-600">
                      {candidate.ineligibility_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}

      {selected && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            Selected: <span className="font-medium">{selected.title}</span>{" "}
            (item {selected.external_listing_id})
          </p>

          {proposalLoading && (
            <p className="text-sm text-slate-500">
              Loading listing detail from eBay…
            </p>
          )}

          {proposalEntries.length > 0 && (
            <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
              <p className="text-sm font-medium">
                Map StockSmith variants to eBay SKUs
              </p>
              {proposalEntries.map((entry) => {
                const key =
                  entry.variant_id === null
                    ? "product"
                    : String(entry.variant_id);
                const attrs = Object.entries(entry.stockssmith_attributes)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(", ");
                return (
                  <label
                    key={key}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <span>
                      {entry.variant_name ?? "(product)"}
                      {attrs && (
                        <span className="text-slate-500"> — {attrs}</span>
                      )}
                      {entry.match_confidence !== "exact" && (
                        <span className="ml-1 text-xs text-amber-600">
                          (
                          {entry.match_confidence === "count_only"
                            ? "check this"
                            : "pick one"}
                          )
                        </span>
                      )}
                    </span>
                    <select
                      disabled={done}
                      className="rounded-md border border-slate-300 px-2 py-1 font-mono text-xs"
                      value={effectiveMapping[key] ?? ""}
                      onChange={(e) =>
                        setMapping((m) => ({ ...m, [key]: e.target.value }))
                      }
                    >
                      <option value="">Select SKU…</option>
                      {selectableSkus.map((sku) => (
                        <option key={sku} value={sku}>
                          {sku}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              })}
            </div>
          )}

          {!done && (
            <>
              <label className="flex items-start gap-2 rounded-md bg-slate-50 p-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={alignSkus}
                  onChange={(e) => setAlignSkus(e.target.checked)}
                />
                <span>
                  Rewrite eBay's SKUs to match StockSmith
                  <span className="block text-xs text-slate-500">
                    Done before migrating, while the SKU is still editable.
                    Leave this off and any mismatch is only reported — you'd
                    then have to fix it in Seller Hub yourself.
                  </span>
                </span>
              </label>

              {/* Migration is one-way on eBay's side, so this is deliberately an
                      explicit acknowledgement rather than a passive warning line. */}
              <label className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                <span>
                  I understand this permanently converts the eBay listing to an
                  Inventory API listing.
                  <span className="block text-xs">
                    This can't be undone from StockSmith or Seller Hub. The
                    listing keeps its item number, watchers and sales history
                    {alignSkus ? ", and its SKUs will be rewritten first" : ""}.
                  </span>
                </span>
              </label>
            </>
          )}

          <ErrorBanner error={adoptMutation.error} />
          {adoptMutation.data && (
            <AdoptionResultBanner result={adoptMutation.data} />
          )}
        </div>
      )}
    </Modal>
  );
}

function AdoptionResultBanner({ result }: { result: AdoptListingResult }) {
  const conflicts = result.units.filter((u) => u.sku_conflict);
  if (conflicts.length === 0) {
    return (
      <p className="rounded-md bg-green-50 p-2 text-sm text-green-800">
        Listing linked successfully
        {result.skus_aligned
          ? " — eBay's SKUs were rewritten to match StockSmith."
          : "."}
      </p>
    );
  }
  return (
    <div className="rounded-md bg-amber-50 p-2 text-sm text-amber-800">
      <p className="font-medium">
        Linked, but {conflicts.length} SKU(s) don't match StockSmith:
      </p>
      <ul className="mt-1 list-inside list-disc">
        {conflicts.map((u) => (
          <li key={u.variant_id ?? "product"}>
            expected <span className="font-mono">{u.expected_sku}</span>, eBay
            has <span className="font-mono">{u.actual_sku}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs">
        Quantity pushes for these units will fail until the SKUs match. Rename
        them in Seller Hub, then re-test sync.
      </p>
    </div>
  );
}
