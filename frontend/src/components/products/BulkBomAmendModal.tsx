import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import { productsApi } from "../../api/products";
import type {
  BulkBomAmendLine,
  BulkBomAmendResult,
  Product,
} from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";

/**
 * Bulk-corrects BOM overrides for every variant sharing an attribute value — "set the
 * filament quantity to 14 for all Large variants".
 *
 * Two-step by design, not for ceremony: the backend cannot distinguish a rule-generated
 * override from one edited by hand, so the preview showing each value it would replace is
 * what makes overwriting consensual rather than silent.
 */
export function BulkBomAmendModal({
  product,
  onClose,
}: {
  product: Product;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["products", product.id, "bom"],
    queryFn: () => productsApi.getBom(product.id),
  });
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
  });
  const { data: variants } = useQuery({
    queryKey: ["products", product.id, "variants"],
    queryFn: () => productsApi.listVariants(product.id),
  });

  const attributeNames = [
    product.variant_attribute1_name,
    product.variant_attribute2_name,
    product.variant_attribute3_name,
  ].filter((n): n is string => !!n);

  const [attributeName, setAttributeName] = useState(attributeNames[0] ?? "");
  const [attributeValue, setAttributeValue] = useState("");

  // The values actually present on this product's variants for the chosen attribute.
  // The backend matches attribute_value literally, so free text meant a typo, a case
  // difference or a stray space silently matched zero variants and returned an empty
  // preview — indistinguishable from "no variants use this value". Offering only real
  // values makes that failure mode unreachable.
  const attributeSlot = attributeNames.indexOf(attributeName);
  const attributeValues = Array.from(
    new Set(
      (variants ?? [])
        .filter((v) => v.is_active)
        .map(
          (v) =>
            [v.attribute1_value, v.attribute2_value, v.attribute3_value][
              attributeSlot
            ],
        )
        .filter((value): value is string => !!value && value.trim() !== ""),
    ),
  );
  const [baseMaterialId, setBaseMaterialId] = useState<number | null>(null);
  const [qty, setQty] = useState("");
  const [substituteId, setSubstituteId] = useState<number | null>(null);
  const [preview, setPreview] = useState<BulkBomAmendResult | null>(null);

  const baseLine = bom?.find((l) => l.material_id === baseMaterialId);
  // Substituting only ever swaps within the same material type — the backend enforces it
  // strictly here (this fans out across many variants without review), so offering
  // anything else in the picker would only produce a 400.
  const baseMaterial = materials?.find((m) => m.id === baseMaterialId);
  const substituteOptions = (materials ?? []).filter(
    (m) =>
      m.id !== baseMaterialId &&
      baseMaterial != null &&
      m.material_type_id === baseMaterial.material_type_id,
  );

  const lines: BulkBomAmendLine[] =
    baseMaterialId === null
      ? []
      : [
          {
            base_material_id: baseMaterialId,
            material_id: substituteId,
            qty_required: qty.trim() === "" ? null : qty.trim(),
          },
        ];

  const amendMutation = useMutation({
    mutationFn: (apply: boolean) =>
      productsApi.amendVariantBomOverrides(product.id, {
        attribute_name: attributeName,
        attribute_value: attributeValue.trim(),
        lines,
        apply,
      }),
    onSuccess: (result) => {
      setPreview(result);
      if (!result.applied) return;
      queryClient.invalidateQueries({ queryKey: ["products", product.id] });
      queryClient.invalidateQueries({
        queryKey: ["products", product.id, "variants"],
      });
      queryClient.invalidateQueries({ queryKey: ["variants"] });
    },
  });

  const canPreview =
    attributeName !== "" &&
    attributeValue.trim() !== "" &&
    baseMaterialId !== null;
  const applied = preview?.applied ?? false;

  return (
    <Modal
      title="Bulk-edit BOM overrides"
      maxWidth="max-w-3xl"
      onClose={amendMutation.isPending ? () => {} : onClose}
      footer={
        <>
          <button
            onClick={onClose}
            className="rounded-md border border-slate-300 px-3 py-1.5"
          >
            {applied ? "Close" : "Cancel"}
          </button>
          {!applied && (
            <button
              onClick={() => amendMutation.mutate(false)}
              disabled={!canPreview || amendMutation.isPending}
              className="rounded-md border border-slate-300 px-3 py-1.5 disabled:opacity-50"
            >
              {amendMutation.isPending && !amendMutation.variables
                ? "Checking…"
                : "Preview"}
            </button>
          )}
          {preview && !applied && preview.changed_variant_count > 0 && (
            <button
              onClick={() => amendMutation.mutate(true)}
              disabled={amendMutation.isPending}
              className="rounded-md bg-slate-900 px-4 py-1.5 text-white disabled:opacity-50"
            >
              {amendMutation.isPending
                ? "Applying…"
                : `Apply to ${preview.changed_variant_count} variant(s)`}
            </button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-slate-600">
          Applies to every variant with the attribute value you choose — for
          example every "Large" variant, regardless of its other attributes.
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span>Attribute</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1"
              value={attributeName}
              onChange={(e) => {
                setAttributeName(e.target.value);
                // A value from the previous attribute would match no variants at all.
                setAttributeValue("");
                setPreview(null);
              }}
            >
              {attributeNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span>Value</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1 disabled:bg-slate-50"
              disabled={attributeValues.length === 0}
              value={attributeValue}
              onChange={(e) => {
                setAttributeValue(e.target.value);
                setPreview(null);
              }}
            >
              <option value="">
                {attributeValues.length === 0
                  ? "No values on this attribute"
                  : "Select a value…"}
              </option>
              {attributeValues.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span>BOM line</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1"
              value={baseMaterialId ?? ""}
              onChange={(e) => {
                setBaseMaterialId(
                  e.target.value === "" ? null : Number(e.target.value),
                );
                setSubstituteId(null);
                setPreview(null);
              }}
            >
              <option value="">Select a line…</option>
              {(bom ?? []).map((line) => (
                <option key={line.material_id} value={line.material_id}>
                  {materials?.find((m) => m.id === line.material_id)?.name ??
                    `#${line.material_id}`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span>Quantity</span>
            <input
              className="w-28 rounded-md border border-slate-300 px-2 py-1"
              placeholder={
                baseLine ? `base ${baseLine.qty_required}` : "inherit"
              }
              value={qty}
              onChange={(e) => {
                setQty(e.target.value);
                setPreview(null);
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span>Substitute with</span>
            <select
              className="rounded-md border border-slate-300 px-2 py-1"
              value={substituteId ?? ""}
              onChange={(e) => {
                setSubstituteId(
                  e.target.value === "" ? null : Number(e.target.value),
                );
                setPreview(null);
              }}
            >
              <option value="">Keep the base material</option>
              {substituteOptions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <ErrorBanner error={amendMutation.error} />

        {preview && (
          <div className="flex flex-col gap-2">
            <p className="text-sm">
              {applied ? "Applied to " : "Would change "}
              <strong>{preview.changed_variant_count}</strong> of{" "}
              {preview.matched_variant_count} matching variant(s)
              {preview.skipped_inactive_count > 0 && (
                <>
                  {" "}
                  ({preview.skipped_inactive_count} inactive variant(s) skipped)
                </>
              )}
              .
            </p>
            {preview.units.filter((u) => u.changes.length > 0).length > 0 ? (
              <div className="max-h-64 overflow-auto">
                <table className="w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-slate-500">
                      <th className="p-1">Variant</th>
                      <th className="p-1">BOM line</th>
                      <th className="p-1">Before</th>
                      <th className="p-1">After</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.units.flatMap((unit) =>
                      unit.changes.map((change) => (
                        <tr
                          key={`${unit.variant_id}-${change.base_material_id}`}
                          className="border-b border-slate-100"
                        >
                          <td className="p-1">{unit.variant_name}</td>
                          <td className="p-1">{change.base_material_name}</td>
                          {/* A non-null "before" means an override already existed and is
                              about to be replaced — highlighted because it may well have
                              been edited by hand, which nothing here can detect. */}
                          <td
                            className={`p-1 ${change.before_qty !== null ? "font-medium text-amber-800" : "text-slate-500"}`}
                          >
                            {describeSide(
                              change.before_material_id,
                              change.before_qty,
                              materials,
                            ) ?? "inherits base BOM"}
                          </td>
                          <td className="p-1">
                            {describeSide(
                              change.after_material_id,
                              change.after_qty,
                              materials,
                            ) ?? "inherits base BOM"}
                          </td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                Every matching variant is already set this way.
              </p>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function describeSide(
  materialId: number | null,
  qty: string | null,
  materials: { id: number; name: string }[] | undefined,
): string | null {
  if (materialId === null && qty === null) return null;
  const name = materials?.find((m) => m.id === materialId)?.name;
  return name ? `${name} × ${qty ?? "—"}` : `× ${qty ?? "—"}`;
}
