import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { platformLimitsApi, type FieldViolation, type ProductCompatibility } from "../../api/platformLimits";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Reports which products breach a platform's field limits, before anything is pushed.
 *
 * Loaded automatically rather than sitting behind a button: this makes no marketplace
 * call, so there is no rate-limit budget to protect and nothing to gain from making the
 * user ask. (The listing gap scan next door is a button for exactly the opposite reason —
 * it spends eBay's Trading quota.)
 *
 * Renders nothing at all when the catalogue is clean. A panel permanently announcing
 * "0 problems" trains people to stop reading it, and this one needs to be noticed on the
 * day it finally says something.
 */
export function PlatformCompatibilityPanel({ platform }: { platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const { data, error } = useQuery({
    queryKey: ["platforms", platform, "catalogue-compatibility"],
    queryFn: () => platformLimitsApi.catalogueCompatibility(platform),
  });

  if (error) return <ErrorBanner error={error} />;
  if (!data || data.products.length === 0) return null;

  const { blocked_count, warning_count, total_products } = data;

  return (
    <div className="flex flex-col gap-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
      <p>
        <strong>{blocked_count + warning_count}</strong> of {total_products} product(s) don't fit {label}'s
        limits
        {blocked_count > 0 && (
          <>
            {" "}
            — <strong className="text-red-700">{blocked_count} blocked</strong>
          </>
        )}
        {warning_count > 0 && <> · {warning_count} need adjusting</>}.
      </p>
      <div className="flex flex-col gap-2">
        {data.products.map((product) => (
          <ProductRow key={product.product_id} product={product} />
        ))}
      </div>
    </div>
  );
}

function ProductRow({ product }: { product: ProductCompatibility }) {
  const [expanded, setExpanded] = useState(false);
  const unitViolationCount = product.units.reduce((sum, unit) => sum + unit.violations.length, 0);

  return (
    <div className={`rounded border bg-white p-2 ${product.is_blocked ? "border-red-300" : "border-slate-200"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/products/$productId" params={{ productId: String(product.product_id) }} className="underline">
            {product.product_name}
          </Link>
          {product.product_sku && <span className="ml-2 font-mono text-xs text-slate-500">{product.product_sku}</span>}
        </div>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs ${
            product.is_blocked ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800"
          }`}
        >
          {product.is_blocked ? "Blocked" : "Needs adjusting"}
        </span>
      </div>

      <ul className="mt-1 list-inside list-disc text-xs">
        {product.violations.map((violation, i) => (
          <ViolationLine key={`${violation.field}-${i}`} violation={violation} />
        ))}
      </ul>

      {unitViolationCount > 0 && (
        <>
          <button onClick={() => setExpanded((v) => !v)} className="mt-1 text-xs text-slate-600 underline">
            {expanded ? "Hide" : `Show ${unitViolationCount} variation issue(s)`}
          </button>
          {expanded && (
            <ul className="mt-1 flex flex-col gap-1 text-xs">
              {product.units.map((unit) => (
                <li key={unit.variant_id ?? "product"}>
                  <span className="font-medium">{unit.variant_name ?? "(product)"}</span>
                  {unit.sku && <span className="ml-2 font-mono text-slate-500">{unit.sku}</span>}
                  <ul className="list-inside list-disc pl-3">
                    {unit.violations.map((violation, i) => (
                      <ViolationLine key={`${violation.field}-${i}`} violation={violation} />
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function ViolationLine({ violation }: { violation: FieldViolation }) {
  return (
    <li className={violation.severity === "blocker" ? "text-red-700" : "text-amber-800"}>
      {violation.message}
      {/* Only shown where the backend judged the fix unambiguous — a blocker never
          carries one, because "just truncate the SKU" is exactly the wrong answer. */}
      {violation.suggested_value && (
        <span className="text-slate-600">
          {" "}
          Suggested: <span className="font-mono">{violation.suggested_value}</span>
        </span>
      )}
    </li>
  );
}
