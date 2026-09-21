import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../../api/client";
import { productsApi } from "../../api/products";
import type { Product, Variant } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { AttributeValueMergeModal } from "./AttributeValueMergeModal";

type Slot = 1 | 2 | 3;

interface SlotValues {
  slot: Slot;
  name: string;
  values: string[]; // distinct, in first-seen order, active and disabled variants alike
}

/**
 * The distinct values each attribute currently takes across the product's variants.
 *
 * Disabled variants are included on purpose: a value that survives only on a disabled
 * variant is still a value, and hiding it here would leave it impossible to rename or
 * merge away until the variant was reactivated.
 */
export function attributeSlotValues(product: Product, variants: Variant[]): SlotValues[] {
  const names = [product.variant_attribute1_name, product.variant_attribute2_name, product.variant_attribute3_name];
  const out: SlotValues[] = [];
  names.forEach((name, index) => {
    if (!name) return;
    const seen = new Set<string>();
    for (const v of variants) {
      const value = [v.attribute1_value, v.attribute2_value, v.attribute3_value][index];
      if (value && value.trim() !== "") seen.add(value);
    }
    out.push({ slot: (index + 1) as Slot, name, values: Array.from(seen) });
  });
  return out;
}

/**
 * Lists every attribute value on the product and lets each one be respelled in place.
 *
 * A value is not a row anywhere — it is a string repeated on each variant — so this is the
 * only place the app shows "4 Stud Standard" as one thing rather than as a badge on each
 * of its variants. The rename never rewrites a SKU (see services/sku_generation on the
 * backend); it changes the label and the display names that were still generated from it.
 */
export function AttributeValuesPanel({ product }: { product: Product }) {
  const { data: variants } = useQuery({
    queryKey: ["products", product.id, "variants"],
    queryFn: () => productsApi.listVariants(product.id),
  });

  const slots = attributeSlotValues(product, variants ?? []);
  if (slots.length === 0 || slots.every((s) => s.values.length === 0)) return null;

  return (
    <div className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm">
      <div>
        <h3 className="text-sm font-medium">Attribute values</h3>
        <p className="text-xs text-slate-500">
          Rename an attribute or one of its values everywhere it appears, or merge two values that mean the same
          thing. SKUs are never changed; marketplace listings show the new name when next pushed.
        </p>
      </div>
      {slots.map((slot) => (
        <div key={slot.slot} className="flex flex-col gap-1">
          <SlotHeading product={product} slot={slot.slot} name={slot.name} />
          <ul className="flex flex-col gap-1">
            {slot.values.map((value) => (
              <ValueRow
                key={value}
                product={product}
                slot={slot.slot}
                attributeName={slot.name}
                value={value}
                siblings={slot.values}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * The attribute's own name, renamable in place. Only a respelling: the slot stays where it
 * is, because variants hold their values by slot number and the server refuses to clear
 * a slot that has any.
 */
function SlotHeading({ product, slot, name }: { product: Product; slot: Slot; name: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);

  const renameMutation = useMutation({
    mutationFn: (newName: string) => productsApi.update(product.id, { [`variant_attribute${slot}_name`]: newName }),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["products", product.id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const trimmed = draft.trim();
  const cancel = () => {
    setEditing(false);
    setDraft(name);
    renameMutation.reset();
  };

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <p className="text-xs font-medium text-slate-600">{name}</p>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="text-xs text-slate-500 underline"
          aria-label={`Rename attribute ${name}`}
        >
          Rename
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <form
        className="flex flex-wrap items-center gap-2 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (!trimmed || trimmed === name) cancel();
          else renameMutation.mutate(trimmed);
        }}
      >
        <input
          autoFocus
          aria-label={`New name for attribute ${name}`}
          className="rounded border border-slate-300 px-2 py-0.5 text-xs"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
          }}
        />
        <button
          type="submit"
          disabled={renameMutation.isPending || !trimmed}
          className="rounded bg-slate-900 px-2 py-0.5 text-xs text-white disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={cancel} className="text-xs text-slate-600 underline">
          Cancel
        </button>
      </form>
      <ErrorBanner error={renameMutation.error} />
    </div>
  );
}

function ValueRow({
  product,
  slot,
  attributeName,
  value,
  siblings,
}: {
  product: Product;
  slot: Slot;
  attributeName: string;
  value: string;
  siblings: string[];
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [notice, setNotice] = useState<string | null>(null);
  // null = closed; a string = open with that survivor preselected ("" = none yet).
  const [mergeInto, setMergeInto] = useState<string | null>(null);
  const others = siblings.filter((s) => s !== value);

  const renameMutation = useMutation({
    mutationFn: (newValue: string) =>
      productsApi.renameAttributeValue(product.id, { slot, old_value: value, new_value: newValue }),
    onSuccess: (result) => {
      setEditing(false);
      setNotice(
        result.live_platforms.length > 0
          ? `Renamed on ${result.variants_updated} variant${result.variants_updated === 1 ? "" : "s"}. ` +
              `${result.live_platforms.join(" and ")} still show the old name until the listing is next pushed.`
          : null,
      );
      queryClient.invalidateQueries({ queryKey: ["products", product.id, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", product.id] });
      queryClient.invalidateQueries({ queryKey: ["variants"] });
    },
  });

  // A 409 means the new spelling is already a sibling value: the two are one thing
  // spelled twice, which is a merge rather than a rename. Named here so the banner can
  // point at the exact value rather than echo the server's sentence.
  const trimmed = draft.trim();
  const conflictValue =
    renameMutation.error instanceof ApiError && renameMutation.error.status === 409
      ? siblings.find((s) => s === trimmed && s !== value) ?? trimmed
      : null;

  const cancel = () => {
    setEditing(false);
    setDraft(value);
    renameMutation.reset();
  };

  const submit = () => {
    if (!trimmed || trimmed === value) {
      cancel();
      return;
    }
    renameMutation.mutate(trimmed);
  };

  const mergeModal = mergeInto != null && (
    <AttributeValueMergeModal
      productId={product.id}
      slot={slot}
      attributeName={attributeName}
      loserValue={value}
      candidates={others}
      initialSurvivor={mergeInto || undefined}
      onClose={() => {
        setMergeInto(null);
        cancel();
      }}
    />
  );

  if (!editing) {
    return (
      <li className="flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded bg-slate-100 px-2 py-0.5">{value}</span>
        <button
          type="button"
          onClick={() => {
            setNotice(null);
            setEditing(true);
          }}
          className="text-xs text-slate-600 underline"
          aria-label={`Rename ${value}`}
        >
          Rename
        </button>
        {others.length > 0 && (
          <button
            type="button"
            onClick={() => setMergeInto("")}
            className="text-xs text-slate-600 underline"
            aria-label={`Merge ${value} into another value`}
          >
            Merge into…
          </button>
        )}
        {notice && <span className="text-xs text-amber-800">{notice}</span>}
        {mergeModal}
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-1">
      {mergeModal}
      <form
        className="flex flex-wrap items-center gap-2 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          autoFocus
          aria-label={`New name for ${value}`}
          className="rounded border border-slate-300 px-2 py-0.5"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            if (renameMutation.error) renameMutation.reset();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
          }}
        />
        <button
          type="submit"
          disabled={renameMutation.isPending || !trimmed}
          className="rounded bg-slate-900 px-2 py-0.5 text-xs text-white disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={cancel} className="text-xs text-slate-600 underline">
          Cancel
        </button>
      </form>
      {conflictValue ? (
        <p className="flex flex-wrap items-center gap-2 rounded bg-amber-50 p-2 text-xs text-amber-900">
          <span>"{conflictValue}" is already a value of this attribute.</span>
          <button type="button" onClick={() => setMergeInto(conflictValue)} className="underline">
            Merge "{value}" into it
          </button>
        </p>
      ) : (
        <ErrorBanner error={renameMutation.error} />
      )}
    </li>
  );
}
