import { useQuery } from "@tanstack/react-query";
import { productsApi } from "../../api/products";
import type { OrderLineInput } from "../../api/orders";
import type { Product } from "../../api/types";

export function OrderLineEditor({
  products,
  lines,
  onChange,
}: {
  products: Product[];
  lines: OrderLineInput[];
  onChange: (lines: OrderLineInput[]) => void;
}) {
  const updateLine = (index: number, patch: Partial<OrderLineInput>) => {
    onChange(lines.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => onChange(lines.filter((_, i) => i !== index));

  const addLine = () => {
    const first = products[0];
    if (!first) return;
    onChange([...lines, { product_id: first.id, variant_id: null, ordered_qty: 1 }]);
  };

  return (
    <div className="flex flex-col gap-2">
      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Product</th>
            <th className="p-2">Variant</th>
            <th className="p-2">Qty ordered</th>
            <th className="p-2">Unit price</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, i) => (
            <OrderLineRow
              key={i}
              products={products}
              line={line}
              onChange={(patch) => updateLine(i, patch)}
              onRemove={() => removeLine(i)}
            />
          ))}
        </tbody>
      </table>
      <button onClick={addLine} className="w-fit rounded border border-slate-300 px-3 py-1.5 text-sm">
        + Add line
      </button>
    </div>
  );
}

function OrderLineRow({
  products,
  line,
  onChange,
  onRemove,
}: {
  products: Product[];
  line: OrderLineInput;
  onChange: (patch: Partial<OrderLineInput>) => void;
  onRemove: () => void;
}) {
  const productId = line.product_id ?? null;
  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId as number),
    enabled: productId != null,
  });
  const hasVariants = (variants?.length ?? 0) > 0;

  return (
    <tr className="border-b border-slate-100">
      <td className="p-2">
        <select
          className="rounded border border-slate-300 px-2 py-1"
          value={productId ?? ""}
          onChange={(e) => onChange({ product_id: Number(e.target.value), variant_id: null })}
        >
          {products.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} {p.sku ? `(${p.sku})` : ""}
            </option>
          ))}
        </select>
      </td>
      <td className="p-2">
        {hasVariants ? (
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={line.variant_id ?? ""}
            onChange={(e) => onChange({ variant_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Select variant…</option>
            {variants?.map((v) => (
              <option key={v.id} value={v.id}>
                {v.variant_name}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="p-2">
        <input
          type="number"
          min={1}
          className="w-24 rounded border border-slate-300 px-2 py-1"
          value={line.ordered_qty}
          onChange={(e) => onChange({ ordered_qty: Number(e.target.value) })}
        />
      </td>
      <td className="p-2">
        <input
          className="w-24 rounded border border-slate-300 px-2 py-1"
          placeholder="0.00"
          value={line.unit_price ?? ""}
          onChange={(e) => onChange({ unit_price: e.target.value || null })}
        />
      </td>
      <td className="p-2">
        <button onClick={onRemove} className="text-red-600">
          Remove
        </button>
      </td>
    </tr>
  );
}
