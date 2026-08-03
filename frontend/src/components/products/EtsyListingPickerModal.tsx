import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type AdoptListingResult, type UnadoptedListing } from "../../api/platforms";
import { productsApi } from "../../api/products";
import { ErrorBanner } from "../common/ErrorBanner";

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
  const [chosenProductId, setChosenProductId] = useState<number | null>(productId ?? null);
  const [selected, setSelected] = useState<UnadoptedListing | null>(null);
  // key: variant_id ?? "product" -> Etsy product index
  const [links, setLinks] = useState<Record<string, number>>({});

  const { data: products } = useQuery({
    queryKey: ["products", "all"],
    queryFn: () => productsApi.list(),
    enabled: productId === undefined,
  });

  const { data: variants } = useQuery({
    queryKey: ["products", chosenProductId, "variants"],
    queryFn: () => productsApi.listVariants(chosenProductId!),
    enabled: chosenProductId !== null,
  });

  const { data: report, isLoading, error } = useQuery({
    queryKey: ["platforms", "etsy", "unadopted-listings"],
    queryFn: () => platformsApi.fetchEtsyUnadoptedListings(),
  });

  const activeVariants = (variants ?? []).filter((v) => v.is_active);
  // Mirrors the backend's own unit model (listing_sync.check_product_sku_sync): a
  // product with no active variants is one unit keyed by variant_id=null.
  const units =
    activeVariants.length > 0
      ? activeVariants.map((v) => ({ key: String(v.id), variantId: v.id as number | null, label: v.variant_name }))
      : [{ key: "product", variantId: null, label: "(product)" }];

  const linksComplete = selected !== null && units.every((u) => links[u.key] !== undefined);

  const adoptMutation = useMutation({
    mutationFn: () => {
      if (!selected || chosenProductId === null) throw new Error("No listing or product selected");
      return platformsApi.adoptEtsyListing(chosenProductId, {
        external_listing_id: selected.external_listing_id,
        listing_title: selected.title,
        links: units.map((u) => ({ variant_id: u.variantId, product_index: links[u.key] })),
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded bg-white shadow-lg">
        <div className="border-b border-slate-200 p-4">
          <h2 className="text-lg font-semibold">Link an Etsy listing</h2>
          {report && !selected && (
            <p className="text-sm text-slate-500">
              {report.total_count} Etsy listing(s) have no matching StockSmith SKU
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
          <ErrorBanner error={error} />

          {!selected && (
            <>
              {productId === undefined && (
                <label className="mb-3 flex flex-col gap-1 text-sm">
                  <span className="text-slate-500">Link to StockSmith product</span>
                  <select
                    className="rounded border border-slate-300 px-2 py-1.5"
                    value={chosenProductId ?? ""}
                    onChange={(e) => setChosenProductId(e.target.value ? Number(e.target.value) : null)}
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
                    Every Etsy listing already matches a StockSmith SKU — nothing to link.
                  </p>
                )}
                {report?.listings.map((listing) => (
                  <button
                    key={listing.external_listing_id}
                    disabled={chosenProductId === null}
                    onClick={() => setSelected(listing)}
                    className={`rounded border p-3 text-left text-sm ${
                      chosenProductId !== null
                        ? "border-slate-200 hover:border-slate-400"
                        : "cursor-not-allowed border-slate-100 bg-slate-50 opacity-60"
                    }`}
                  >
                    <p className="font-medium">{listing.title}</p>
                    <p className="text-xs text-slate-500">
                      Listing {listing.external_listing_id} · {listing.state} · {listing.products.length} variation(s)
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
                Selected: <span className="font-medium">{selected.title}</span> (listing{" "}
                {selected.external_listing_id})
              </p>

              <div className="flex flex-col gap-2 rounded border border-slate-200 p-3">
                <p className="text-sm font-medium">Map StockSmith units to Etsy variations</p>
                {units.map((unit) => (
                  <label key={unit.key} className="flex items-center justify-between gap-2 text-sm">
                    <span>{unit.label}</span>
                    <select
                      disabled={done}
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      value={links[unit.key] ?? ""}
                      onChange={(e) =>
                        setLinks((l) => ({ ...l, [unit.key]: Number(e.target.value) }))
                      }
                    >
                      <option value="">Select Etsy variation…</option>
                      {selected.products.map((p) => (
                        <option key={p.index} value={p.index}>
                          {p.variation ?? `Variation ${p.index + 1}`} — {p.sku ?? "no SKU"} (qty {p.quantity})
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>

              {!done && (
                <p className="rounded bg-slate-50 p-2 text-xs text-slate-600">
                  StockSmith's SKU will be written onto each mapped Etsy variation, replacing whatever is there now.
                  Nothing else on the listing (price, quantity, options) is changed.
                </p>
              )}

              <ErrorBanner error={adoptMutation.error} />
              {adoptMutation.data && <EtsyAdoptionResultBanner result={adoptMutation.data} />}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 p-4">
          {selected && !done && (
            <button onClick={() => setSelected(null)} className="rounded border border-slate-300 px-4 py-2 text-sm">
              Back
            </button>
          )}
          <button onClick={onClose} className="rounded border border-slate-300 px-4 py-2 text-sm">
            {done ? "Close" : "Cancel"}
          </button>
          {selected && !done && (
            <button
              onClick={() => adoptMutation.mutate()}
              disabled={adoptMutation.isPending || !linksComplete}
              className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {adoptMutation.isPending ? "Linking…" : "Write SKUs & link"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function EtsyAdoptionResultBanner({ result }: { result: AdoptListingResult }) {
  return (
    <p className="rounded bg-green-50 p-2 text-sm text-green-800">
      Linked {result.units.length} unit(s) — Etsy now carries StockSmith's SKUs for this listing.
    </p>
  );
}
