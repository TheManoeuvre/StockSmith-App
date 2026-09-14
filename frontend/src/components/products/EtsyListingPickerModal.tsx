import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  platformsApi,
  type AdoptListingResult,
  type AttributeMap,
  type UnadoptedListing,
} from "../../api/platforms";
import { productsApi } from "../../api/products";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { AttributePairingSection } from "./AttributePairingSection";
import { VariantMatchLabel } from "./VariantMatchLabel";

// The Etsy counterpart to ListingPickerModal, and deliberately a separate component
// rather than a `platform` prop on that one: the two flows only look alike. eBay's is
// "the marketplace can't see this listing until it's migrated" (irreversible migration,
// eligibility rules, variation mapping against eBay's own SKUs); Etsy's is "the listing
// is plainly visible, StockSmith just has no SKU for it" (no migration, no eligibility,
// and the SKUs are written by us rather than read from them). Merging them would mean a
// component that is mostly branches.
export function EtsyListingPickerModal({
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
  const [selected, setSelected] = useState<UnadoptedListing | null>(null);
  // key: variant_id ?? "product" -> Etsy product index. Only the user's own picks;
  // the proposal's pre-fill is layered underneath in effectiveLinks.
  const [links, setLinks] = useState<Record<string, number>>({});
  // The user's manual attribute-name pairing, layered over the proposal's own.
  const [attributeOverrides, setAttributeOverrides] = useState<AttributeMap>(
    {},
  );

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
    queryKey: ["platforms", "etsy", "unadopted-listings"],
    queryFn: () => platformsApi.fetchEtsyUnadoptedListings(),
  });

  // Keyed on the product too: the proposal pairs THIS product's units against the
  // listing's products, so a cached one from another product would be for the wrong
  // variant set. The unit model (one row per active variant, or one "product" row when
  // there are none) is the backend's — it comes back as the proposal's entries.
  const {
    data: proposal,
    isLoading: proposalLoading,
    error: proposalError,
  } = useQuery({
    queryKey: [
      "platforms",
      "etsy",
      "variation-mapping",
      chosenProductId,
      selected?.external_listing_id,
      attributeOverrides,
    ],
    queryFn: () =>
      platformsApi.fetchEtsyVariationMapping(
        chosenProductId!,
        selected!.external_listing_id,
        attributeOverrides,
      ),
    enabled: selected !== null && chosenProductId !== null,
  });

  const proposalEntries = useMemo(() => proposal?.entries ?? [], [proposal]);

  const effectiveLinks = useMemo(() => {
    const result: Record<string, number | undefined> = {};
    for (const entry of proposalEntries) {
      const key =
        entry.variant_id === null ? "product" : String(entry.variant_id);
      result[key] = links[key] ?? entry.matched_index ?? undefined;
    }
    return result;
  }, [proposalEntries, links]);

  // A re-pair changes what every row was pre-filled from, so manual row picks made
  // under the old pairing are dropped rather than silently kept over a new proposal.
  const repairAttribute = (stocksmithName: string, platformName: string | null) => {
    setAttributeOverrides((o) => ({ ...o, [stocksmithName]: platformName }));
    setLinks({});
  };

  const linksComplete =
    proposalEntries.length > 0 &&
    proposalEntries.every(
      (e) =>
        effectiveLinks[e.variant_id === null ? "product" : String(e.variant_id)] !==
        undefined,
    );

  const adoptMutation = useMutation({
    mutationFn: () => {
      if (!selected || chosenProductId === null)
        throw new Error("No listing or product selected");
      return platformsApi.adoptEtsyListing(chosenProductId, {
        external_listing_id: selected.external_listing_id,
        listing_title: selected.title,
        links: proposalEntries.map((e) => ({
          variant_id: e.variant_id,
          product_index: effectiveLinks[
            e.variant_id === null ? "product" : String(e.variant_id)
          ]!,
        })),
        write_skus: true,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", "etsy"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const done = adoptMutation.data !== undefined;

  return (
    <Modal
      title="Link an Etsy listing"
      subtitle={
        report &&
        !selected && (
          <p className="text-sm text-slate-500">
            {report.total_count} Etsy listing(s) have no matching StockSmith SKU
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
              disabled={adoptMutation.isPending || !linksComplete}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {adoptMutation.isPending ? "Linking…" : "Write SKUs & link"}
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
                Every Etsy listing already matches a StockSmith SKU — nothing to
                link.
              </p>
            )}
            {report?.listings.map((listing) => (
              <button
                key={listing.external_listing_id}
                disabled={chosenProductId === null}
                onClick={() => setSelected(listing)}
                className={`rounded-md border p-3 text-left text-sm ${
                  chosenProductId !== null
                    ? "border-slate-200 hover:border-slate-400"
                    : "cursor-not-allowed border-slate-100 bg-slate-50 opacity-60"
                }`}
              >
                <p className="font-medium">{listing.title}</p>
                <p className="text-xs text-slate-500">
                  Listing {listing.external_listing_id} · {listing.state} ·{" "}
                  {listing.products.length} variation(s)
                </p>
                <p className="mt-0.5 font-mono text-xs text-slate-500">
                  {listing.products.map((p) => p.sku ?? "(no SKU)").join(", ")}
                </p>
              </button>
            ))}
          </div>
        </>
      )}

      {selected && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            Selected: <span className="font-medium">{selected.title}</span>{" "}
            (listing {selected.external_listing_id})
          </p>

          {proposalLoading && (
            <p className="text-sm text-slate-500">
              Loading listing detail from Etsy…
            </p>
          )}
          <ErrorBanner error={proposalError} />

          {proposal && (
            <AttributePairingSection
              pairs={proposal.attribute_pairs}
              platformNames={proposal.platform_attribute_names}
              overrides={attributeOverrides}
              onChange={repairAttribute}
              platformLabel="Etsy"
              disabled={done}
            />
          )}

          {proposalEntries.length > 0 && (
            <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
              <p className="text-sm font-medium">
                Map StockSmith units to Etsy variations
              </p>
              {proposalEntries.map((entry) => {
                const key =
                  entry.variant_id === null
                    ? "product"
                    : String(entry.variant_id);
                return (
                  <label
                    key={key}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <VariantMatchLabel entry={entry} />
                    <select
                      disabled={done}
                      aria-label={`Etsy variation for ${entry.variant_name ?? "product"}`}
                      className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                      value={effectiveLinks[key] ?? ""}
                      onChange={(e) =>
                        setLinks((l) => ({
                          ...l,
                          [key]: Number(e.target.value),
                        }))
                      }
                    >
                      <option value="">Select Etsy variation…</option>
                      {selected.products.map((p) => (
                        <option key={p.index} value={p.index}>
                          {p.variation ?? `Variation ${p.index + 1}`} —{" "}
                          {p.sku ?? "no SKU"} (qty {p.quantity})
                        </option>
                      ))}
                    </select>
                  </label>
                );
              })}
            </div>
          )}

          {!done && (
            <p className="rounded-md bg-slate-50 p-2 text-xs text-slate-600">
              StockSmith's SKU will be written onto each mapped Etsy variation,
              replacing whatever is there now. Nothing else on the listing
              (price, quantity, options) is changed.
            </p>
          )}

          <ErrorBanner error={adoptMutation.error} />
          {adoptMutation.data && (
            <EtsyAdoptionResultBanner result={adoptMutation.data} />
          )}
        </div>
      )}
    </Modal>
  );
}

function EtsyAdoptionResultBanner({ result }: { result: AdoptListingResult }) {
  return (
    <p className="rounded-md bg-green-50 p-2 text-sm text-green-800">
      Linked {result.units.length} unit(s) — Etsy now carries StockSmith's SKUs
      for this listing.
    </p>
  );
}
