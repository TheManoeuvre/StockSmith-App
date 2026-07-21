import type { Product } from "../../api/types";

export function ProductSelect({
  products,
  value,
  onChange,
  className,
}: {
  products: Product[];
  value: number;
  onChange: (productId: number) => void;
  className?: string;
}) {
  return (
    <select
      className={className ?? "rounded border border-slate-300 px-2 py-1"}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      {products.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name} {p.sku ? `(${p.sku})` : ""}
        </option>
      ))}
    </select>
  );
}
