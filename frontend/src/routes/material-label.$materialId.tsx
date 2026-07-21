import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { materialsApi } from "../api/materials";
import { BarcodeLabel } from "../components/common/BarcodeLabel";

export const Route = createFileRoute("/material-label/$materialId")({
  component: MaterialLabel,
});

function MaterialLabel() {
  const { materialId } = Route.useParams();
  const id = Number(materialId);
  const { data: material } = useQuery({ queryKey: ["materials", id], queryFn: () => materialsApi.get(id) });

  if (!material) return <p>Loading…</p>;
  if (!material.barcode) return <p>This material has no barcode set.</p>;

  return (
    <div className="flex flex-col items-center gap-4">
      <BarcodeLabel name={material.name} barcode={material.barcode} />
      <button onClick={() => window.print()} className="rounded bg-slate-900 px-4 py-2 text-white print:hidden">
        Print
      </button>
    </div>
  );
}
