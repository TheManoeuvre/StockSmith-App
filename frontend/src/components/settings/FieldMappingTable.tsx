import { SettingsCard } from "./SettingsCard";

/**
 * A static, read-only reference showing which StockSmith fields actually travel to each
 * marketplace when stock is pushed. Purely informational — nothing here is configurable yet,
 * so it renders straight from a constant rather than an API call.
 */
const ROWS: { field: string; etsy: string; ebay: string }[] = [
  { field: "SKU", etsy: "Mapped", ebay: "Mapped" },
  { field: "Sellable quantity", etsy: "Pushed", ebay: "Pushed" },
  { field: "Variant", etsy: "Mapped", ebay: "Not mapped" },
  { field: "Price", etsy: "Not pushed", ebay: "Not pushed" },
];

const toneFor = (value: string) =>
  value === "Not mapped" || value === "Not pushed" ? "text-slate-400" : "text-slate-700";

export function FieldMappingTable() {
  return (
    <SettingsCard title="Field mapping" help="Which StockSmith fields travel to each connected store.">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
              <th className="p-1.5">StockSmith</th>
              <th className="p-1.5">Etsy</th>
              <th className="p-1.5">eBay</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => (
              <tr key={row.field} className="border-b border-slate-100">
                <td className="p-1.5 font-medium">{row.field}</td>
                <td className={`p-1.5 ${toneFor(row.etsy)}`}>{row.etsy}</td>
                <td className={`p-1.5 ${toneFor(row.ebay)}`}>{row.ebay}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SettingsCard>
  );
}
