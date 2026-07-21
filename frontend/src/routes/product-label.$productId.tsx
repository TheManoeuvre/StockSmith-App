import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { productsApi } from "../api/products";
import { BarcodeLabel } from "../components/common/BarcodeLabel";

export const Route = createFileRoute("/product-label/$productId")({
  component: ProductLabel,
});

function ProductLabel() {
  const { productId } = Route.useParams();
  const id = Number(productId);
  const { data: product } = useQuery({ queryKey: ["products", id], queryFn: () => productsApi.get(id) });

  if (!product) return <p>Loading…</p>;
  if (!product.barcode) return <p>This product has no barcode set.</p>;

  return (
    <div className="flex flex-col items-center gap-4">
      <BarcodeLabel name={product.name} sku={product.sku} barcode={product.barcode} />
      <button onClick={() => window.print()} className="rounded bg-slate-900 px-4 py-2 text-white print:hidden">
        Print
      </button>
    </div>
  );
}
