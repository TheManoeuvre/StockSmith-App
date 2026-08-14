import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  etsyBackfillApi,
  type BackfillField,
  type ProductBackfillProposal,
} from "../../api/etsyBackfill";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Fills blank descriptions, prices and hero images from the Etsy listings a product is
 * already matched to.
 *
 * Behind a button rather than auto-loading, unlike the compatibility panel next door:
 * this one really does call Etsy, and a shop-wide crawl on every settings page view is
 * not free. Preview first, tick what to take, then apply — the apply re-crawls and
 * re-derives rather than trusting what was previewed.
 *
 * Nothing already filled in is ever overwritten, so re-running is safe and the second run
 * simply reports nothing left to do.
 */
export function EtsyBackfillPanel() {
  const queryClient = useQueryClient();
  const [selections, setSelections] = useState<Record<number, Set<BackfillField>>>({});

  const previewMutation = useMutation({
    mutationFn: () => etsyBackfillApi.preview(),
    onSuccess: (data) => {
      // Default to taking everything on offer: the common case is "yes, all of it", and
      // the tick boxes are there for the exception.
      const next: Record<number, Set<BackfillField>> = {};
      for (const product of data.products) next[product.product_id] = new Set(availableFields(product));
      setSelections(next);
    },
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      etsyBackfillApi.apply(
        Object.entries(selections)
          .filter(([, fields]) => fields.size > 0)
          .map(([productId, fields]) => ({ product_id: Number(productId), fields: [...fields] }))
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      previewMutation.mutate();
    },
  });

  const preview = previewMutation.data;
  const selectedCount = Object.values(selections).filter((fields) => fields.size > 0).length;

  function toggle(productId: number, field: BackfillField) {
    setSelections((current) => {
      const fields = new Set(current[productId] ?? []);
      if (fields.has(field)) fields.delete(field);
      else fields.add(field);
      return { ...current, [productId]: fields };
    });
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-medium">Backfill from Etsy</p>
          <p className="text-xs text-slate-500">
            Copies descriptions, prices and hero images from listings already linked to your products. Never
            overwrites anything you've already filled in.
          </p>
        </div>
        <button
          onClick={() => previewMutation.mutate()}
          disabled={previewMutation.isPending}
          className="shrink-0 rounded border border-slate-300 px-3 py-1.5 disabled:opacity-50"
        >
          {previewMutation.isPending ? "Checking…" : "Check Etsy"}
        </button>
      </div>

      <ErrorBanner error={previewMutation.error} />
      <ErrorBanner error={applyMutation.error} />

      {preview && preview.products.length === 0 && (
        <p className="text-slate-600">
          Nothing to fill in. {preview.already_complete} matched product(s) are already complete
          {preview.unmatched > 0 && `, ${preview.unmatched} aren't linked to an Etsy listing`}.
        </p>
      )}

      {preview && preview.products.length > 0 && (
        <>
          <p className="text-slate-600">
            {preview.products.length} product(s) have something to fill in.
            {preview.already_complete > 0 && ` ${preview.already_complete} already complete.`}
            {preview.unmatched > 0 && ` ${preview.unmatched} not linked to an Etsy listing.`}
          </p>
          <div className="flex flex-col gap-2">
            {preview.products.map((product) => (
              <ProposalRow
                key={product.product_id}
                product={product}
                selected={selections[product.product_id] ?? new Set()}
                onToggle={(field) => toggle(product.product_id, field)}
              />
            ))}
          </div>
          <button
            onClick={() => applyMutation.mutate()}
            disabled={applyMutation.isPending || selectedCount === 0}
            className="self-start rounded border border-slate-400 bg-white px-3 py-1.5 disabled:opacity-50"
          >
            {applyMutation.isPending ? "Filling…" : `Fill ${selectedCount} product(s)`}
          </button>
        </>
      )}

      {applyMutation.data && (
        <div className="rounded bg-slate-50 p-2">
          Updated <strong>{applyMutation.data.products_updated}</strong> product(s) —{" "}
          {applyMutation.data.descriptions_filled} description(s), {applyMutation.data.prices_filled} price(s),{" "}
          {applyMutation.data.images_filled} image(s).
          {applyMutation.data.errors.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-xs text-red-700">
              {applyMutation.data.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function availableFields(product: ProductBackfillProposal): BackfillField[] {
  const fields: BackfillField[] = [];
  if (product.description) fields.push("description");
  if (product.sale_price !== null || product.variant_prices.length > 0) fields.push("price");
  if (product.image_url) fields.push("image");
  return fields;
}

function ProposalRow({
  product,
  selected,
  onToggle,
}: {
  product: ProductBackfillProposal;
  selected: Set<BackfillField>;
  onToggle: (field: BackfillField) => void;
}) {
  const priceLabel =
    product.variant_prices.length > 0
      ? `Price (${product.variant_prices.length} variant(s))`
      : `Price (${product.sale_price})`;

  return (
    <div className="rounded border border-slate-200 p-2">
      <p className="font-medium">{product.product_name}</p>
      <div className="mt-1 flex flex-wrap gap-3 text-xs">
        {availableFields(product).map((field) => (
          <label key={field} className="flex items-center gap-1">
            <input type="checkbox" checked={selected.has(field)} onChange={() => onToggle(field)} />
            <span>
              {field === "description" && `Description (${product.description_chars} chars)`}
              {field === "price" && priceLabel}
              {field === "image" && "Hero image"}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
