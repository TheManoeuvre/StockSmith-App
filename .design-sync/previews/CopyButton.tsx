import { CopyButton } from "stocksmith-ui";

/** Beside a SKU, as in the product header. */
export const BesideSku = () => (
  <div className="flex items-center gap-1 text-sm text-slate-600">
    <span className="font-mono">CST-WAL-4</span>
    <CopyButton value="CST-WAL-4" label="Copy SKU" />
  </div>
);

export const Standalone = () => <CopyButton value="Walnut coaster set" label="Copy product name" />;
