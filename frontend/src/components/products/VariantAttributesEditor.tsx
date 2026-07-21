import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { materialsApi } from "../../api/materials";
import { productsApi } from "../../api/products";
import type { AttributeMaterialRule, AttributeQuantityRule, Material, Product, VariantAttributeSpec } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

interface MaterialRuleState {
  baseMaterialId: number;
  valueToMaterialId: Record<string, number>;
}

interface QuantityRuleState {
  baseMaterialId: number;
  valueToQty: Record<string, string>;
}

interface AttributeRow {
  name: string;
  valuesText: string;
  materialRules: MaterialRuleState[];
  quantityRules: QuantityRuleState[];
}

const emptyRow: AttributeRow = { name: "", valuesText: "", materialRules: [], quantityRules: [] };

function splitValues(valuesText: string): string[] {
  return valuesText
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

export function VariantAttributesEditor({ product }: { product: Product }) {
  const queryClient = useQueryClient();
  const { data: colours } = useQuery({ queryKey: ["materials", "colours"], queryFn: materialsApi.listColours });
  const { data: bom } = useQuery({
    queryKey: ["products", product.id, "bom"],
    queryFn: () => productsApi.getBom(product.id),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const colourListId = useId();

  const [rows, setRows] = useState<AttributeRow[]>([emptyRow]);

  useEffect(() => {
    const existing = [product.variant_attribute1_name, product.variant_attribute2_name, product.variant_attribute3_name]
      .filter((n): n is string => !!n)
      .map((name) => ({ name, valuesText: "", materialRules: [], quantityRules: [] }));
    if (existing.length > 0) setRows(existing);
  }, [product.id]);

  const generateMutation = useMutation({
    mutationFn: () => {
      const attributes: VariantAttributeSpec[] = rows
        .filter((r) => r.name.trim())
        .map((r) => ({
          name: r.name.trim(),
          values: splitValues(r.valuesText),
          material_rules: r.materialRules
            .filter((mr) => Object.keys(mr.valueToMaterialId).length > 0)
            .map(
              (mr): AttributeMaterialRule => ({
                base_material_id: mr.baseMaterialId,
                value_to_material_id: mr.valueToMaterialId,
              })
            ),
          quantity_rules: r.quantityRules
            .filter((qr) => Object.keys(qr.valueToQty).length > 0)
            .map(
              (qr): AttributeQuantityRule => ({ base_material_id: qr.baseMaterialId, value_to_qty: qr.valueToQty })
            ),
        }));
      return productsApi.generateVariants(product.id, attributes);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", product.id, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products", product.id] });
      setRows((prev) => prev.map((r) => ({ ...r, valuesText: "", materialRules: [], quantityRules: [] })));
    },
  });

  const updateRow = (index: number, patch: Partial<AttributeRow>) => {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  };

  const addRow = () => {
    if (rows.length >= 3) return;
    setRows((prev) => [...prev, emptyRow]);
  };

  const removeRow = (index: number) => setRows((prev) => prev.filter((_, i) => i !== index));

  const isColourAttribute = (name: string) => /^colou?r$/i.test(name.trim());

  return (
    <div className="flex flex-col gap-2 rounded bg-white p-4 shadow-sm">
      {rows.map((row, i) => {
        const values = splitValues(row.valuesText);
        return (
          <div key={i} className="flex flex-col gap-2 border-b border-slate-100 pb-3 last:border-0">
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-sm">Attribute name</span>
                <input
                  className="w-32 rounded border border-slate-300 px-2 py-1"
                  placeholder="Size, Colour…"
                  value={row.name}
                  onChange={(e) => updateRow(i, { name: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 flex-1">
                <span className="text-sm">
                  Values (comma-separated){row.materialRules.length > 0 && " — derived from checked materials below"}
                </span>
                <input
                  className="w-full rounded border border-slate-300 px-2 py-1 disabled:bg-slate-50 disabled:text-slate-500"
                  placeholder="Small, Medium, Large"
                  value={row.valuesText}
                  disabled={row.materialRules.length > 0}
                  onChange={(e) => updateRow(i, { valuesText: e.target.value })}
                  list={isColourAttribute(row.name) ? colourListId : undefined}
                />
              </label>
              <button onClick={() => removeRow(i)} className="text-red-600">
                Remove
              </button>
            </div>

            {row.name.trim() && bom && bom.length > 0 && materials && (
              <div className="flex flex-col gap-2 rounded border border-slate-200 bg-slate-50 p-2">
                <p className="text-xs font-medium text-slate-600">BOM rules for "{row.name.trim()}"</p>
                {bom.map((line) => {
                  const baseMaterial = materials.find((m) => m.id === line.material_id);
                  if (!baseMaterial) return null;
                  const materialRule = row.materialRules.find((mr) => mr.baseMaterialId === line.material_id);
                  const quantityRule = row.quantityRules.find((qr) => qr.baseMaterialId === line.material_id);
                  const isDrivingValues = row.materialRules[0]?.baseMaterialId === line.material_id;

                  return (
                    <div
                      key={line.material_id}
                      className="flex flex-col gap-1 rounded border border-slate-200 bg-white p-2 text-sm"
                    >
                      <p className="font-medium">{baseMaterial.name}</p>
                      <label className="flex items-center gap-1 text-xs">
                        <input
                          type="checkbox"
                          checked={!!materialRule}
                          onChange={(e) => {
                            if (e.target.checked) {
                              const isFirst = row.materialRules.length === 0;
                              const originalLabel = baseMaterial.colour || baseMaterial.name;
                              const newRule: MaterialRuleState = {
                                baseMaterialId: line.material_id,
                                valueToMaterialId: isFirst ? { [originalLabel]: baseMaterial.id } : {},
                              };
                              updateRow(i, {
                                materialRules: [...row.materialRules, newRule],
                                ...(isFirst ? { valuesText: originalLabel } : {}),
                              });
                            } else {
                              updateRow(i, {
                                materialRules: row.materialRules.filter((mr) => mr.baseMaterialId !== line.material_id),
                              });
                            }
                          }}
                        />
                        Material driven by this attribute
                      </label>
                      {materialRule && (
                        <MaterialRulePanel
                          baseMaterial={baseMaterial}
                          rule={materialRule}
                          isDrivingValues={isDrivingValues}
                          existingValues={values}
                          onChangeRule={(next) =>
                            updateRow(i, {
                              materialRules: row.materialRules.map((mr) =>
                                mr.baseMaterialId === line.material_id ? next : mr
                              ),
                            })
                          }
                          onDeriveValues={(valuesText) => updateRow(i, { valuesText })}
                        />
                      )}

                      <label className="flex items-center gap-1 text-xs">
                        <input
                          type="checkbox"
                          checked={!!quantityRule}
                          onChange={(e) => {
                            if (e.target.checked) {
                              updateRow(i, {
                                quantityRules: [
                                  ...row.quantityRules,
                                  { baseMaterialId: line.material_id, valueToQty: {} },
                                ],
                              });
                            } else {
                              updateRow(i, {
                                quantityRules: row.quantityRules.filter((qr) => qr.baseMaterialId !== line.material_id),
                              });
                            }
                          }}
                        />
                        Quantity driven by this attribute
                      </label>
                      {quantityRule && (
                        <QuantityRulePanel
                          baseLineQty={line.qty_required}
                          rule={quantityRule}
                          existingValues={values}
                          onChangeRule={(next) =>
                            updateRow(i, {
                              quantityRules: row.quantityRules.map((qr) =>
                                qr.baseMaterialId === line.material_id ? next : qr
                              ),
                            })
                          }
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      {colours && (
        <datalist id={colourListId}>
          {colours.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
      )}
      <div className="flex gap-2">
        {rows.length < 3 && (
          <button onClick={addRow} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
            + Add attribute
          </button>
        )}
        <button
          onClick={() => generateMutation.mutate()}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
        >
          Generate variants
        </button>
      </div>
      <ErrorBanner error={generateMutation.error} />
    </div>
  );
}

function MaterialRulePanel({
  baseMaterial,
  rule,
  isDrivingValues,
  existingValues,
  onChangeRule,
  onDeriveValues,
}: {
  baseMaterial: Material;
  rule: MaterialRuleState;
  isDrivingValues: boolean;
  existingValues: string[];
  onChangeRule: (next: MaterialRuleState) => void;
  onDeriveValues: (valuesText: string) => void;
}) {
  const { data: siblings } = useQuery({
    queryKey: ["materials", "by-type", baseMaterial.material_type_id],
    queryFn: () => materialsApi.listByType(baseMaterial.material_type_id as number),
    enabled: baseMaterial.material_type_id != null,
  });

  if (baseMaterial.material_type_id == null) {
    return (
      <p className="pl-4 text-xs text-amber-700">
        "{baseMaterial.name}" has no Material Type set — assign one (in Materials) to group its colour variants
        before this can drive substitutions.
      </p>
    );
  }

  if (!siblings) return <p className="pl-4 text-xs text-slate-400">Loading materials…</p>;

  if (isDrivingValues) {
    const checkedIds = new Set(Object.values(rule.valueToMaterialId));
    const toggle = (material: Material) => {
      const next = { ...rule.valueToMaterialId };
      if (checkedIds.has(material.id)) {
        const key = Object.keys(next).find((k) => next[k] === material.id);
        if (key) delete next[key];
      } else {
        next[material.colour || material.name] = material.id;
      }
      onChangeRule({ ...rule, valueToMaterialId: next });
      onDeriveValues(Object.keys(next).join(", "));
    };

    return (
      <div className="flex flex-wrap gap-2 pl-4">
        {siblings.map((m) => (
          <label key={m.id} className="flex items-center gap-1 text-xs">
            <input type="checkbox" checked={checkedIds.has(m.id)} onChange={() => toggle(m)} />
            {m.colour || m.name}
            {m.id === baseMaterial.id && " (original)"}
          </label>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 pl-4">
      {existingValues.length === 0 && <p className="text-xs text-slate-400">Define this attribute's values first.</p>}
      {existingValues.map((value) => (
        <label key={value} className="flex items-center gap-2 text-xs">
          <span className="w-24">{value}</span>
          <select
            className="rounded border border-slate-300 px-1 py-0.5"
            value={rule.valueToMaterialId[value] ?? baseMaterial.id}
            onChange={(e) =>
              onChangeRule({
                ...rule,
                valueToMaterialId: { ...rule.valueToMaterialId, [value]: Number(e.target.value) },
              })
            }
          >
            {siblings.map((m) => (
              <option key={m.id} value={m.id}>
                {m.colour || m.name}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

function QuantityRulePanel({
  baseLineQty,
  rule,
  existingValues,
  onChangeRule,
}: {
  baseLineQty: string;
  rule: QuantityRuleState;
  existingValues: string[];
  onChangeRule: (next: QuantityRuleState) => void;
}) {
  return (
    <div className="flex flex-col gap-1 pl-4">
      {existingValues.length === 0 && <p className="text-xs text-slate-400">Define this attribute's values first.</p>}
      {existingValues.map((value) => (
        <label key={value} className="flex items-center gap-2 text-xs">
          <span className="w-24">{value}</span>
          <input
            className="w-20 rounded border border-slate-300 px-1 py-0.5"
            value={rule.valueToQty[value] ?? baseLineQty}
            onChange={(e) => onChangeRule({ ...rule, valueToQty: { ...rule.valueToQty, [value]: e.target.value } })}
          />
        </label>
      ))}
    </div>
  );
}
