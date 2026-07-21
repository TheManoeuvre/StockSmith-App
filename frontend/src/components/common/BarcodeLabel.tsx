import JsBarcode from "jsbarcode";
import { useEffect, useRef } from "react";

export function BarcodeLabel({ name, sku, barcode }: { name: string; sku?: string | null; barcode: string }) {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (svgRef.current) {
      JsBarcode(svgRef.current, barcode, { format: "CODE128", height: 40, fontSize: 12, margin: 4 });
    }
  }, [barcode]);

  return (
    <div className="label-print flex flex-col items-center gap-1 p-2 text-center">
      <p className="text-sm font-medium leading-tight">{name}</p>
      {sku && <p className="text-xs text-slate-500 leading-tight">{sku}</p>}
      <svg ref={svgRef} />
      <style>{`
        @media print {
          @page { size: 50mm 25mm; margin: 0; }
          body * { visibility: hidden; }
          .label-print, .label-print * { visibility: visible; }
          .label-print { position: fixed; top: 0; left: 0; width: 50mm; }
        }
      `}</style>
    </div>
  );
}
