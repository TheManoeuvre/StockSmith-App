import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { productCategoriesApi } from "../../api/productCategories";
import { workApi } from "../../api/work";
import { useWorkFlowEnabled } from "../../hooks/useWorkFlow";

/**
 * Choose a work-flow route for one product, overriding its category's. Left on the first option the
 * product inherits (category, then the shop default) — the same inherit-by-default arrangement stock
 * counting uses. Renders nothing until the work-flow feature is switched on.
 */
export function WorkRouteField({
  value,
  onChange,
  categoryId,
  label,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
  categoryId: number | null;
  label: (text: string, control: ReactElement) => ReactElement;
}) {
  const enabled = useWorkFlowEnabled();
  const { data: routes } = useQuery({ queryKey: ["work", "routes"], queryFn: workApi.routes, enabled });
  const { data: categories } = useQuery({ queryKey: ["product-categories"], queryFn: productCategoriesApi.list, enabled });
  if (!enabled) return null;

  const inheritedRouteId = categories?.find((c) => c.id === categoryId)?.work_route_id ?? null;
  const inherited = routes?.find((r) => r.id === inheritedRouteId) ?? routes?.find((r) => r.is_default);
  const inheritLabel = inheritedRouteId !== null ? "category" : "shop default";

  return label(
    "Work route",
    <select
      aria-label="Work route"
      className="rounded border border-slate-300 px-2 py-1"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    >
      <option value="">
        Follow the {inheritLabel}
        {inherited ? ` (${inherited.name})` : ""}
      </option>
      {(routes ?? []).map((route) => (
        <option key={route.id} value={route.id}>
          {route.name}
        </option>
      ))}
    </select>,
  );
}
