import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { productCategoriesApi } from "../../../api/productCategories";
import { workApi } from "../../../api/work";
import { ErrorBanner } from "../../common/ErrorBanner";
import { SettingsCard } from "../SettingsCard";

/**
 * Which route each product category follows. A product can deviate from its category on its own page;
 * anything with no choice anywhere follows the shop default route.
 */
export function CategoryRoutes() {
  const queryClient = useQueryClient();
  const { data: categories } = useQuery({ queryKey: ["product-categories"], queryFn: productCategoriesApi.list });
  const { data: routes } = useQuery({ queryKey: ["work", "routes"], queryFn: workApi.routes });
  const set = useMutation({
    mutationFn: ({ id, name, routeId }: { id: number; name: string; routeId: number | null }) =>
      productCategoriesApi.update(id, { name, work_route_id: routeId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-categories"] });
      queryClient.invalidateQueries({ queryKey: ["work"] });
    },
  });
  const fallback = routes?.find((r) => r.is_default);

  return (
    <SettingsCard
      title="Product categories"
      help="Choose the route each kind of product follows. Individual products can override this on their own page."
    >
      {categories && categories.length === 0 ? (
        <p className="text-sm text-slate-500">No product categories yet. Add one on a product, then pick its route here.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {(categories ?? []).map((category) => (
            <li key={category.id} className="flex items-center gap-3 text-sm">
              <span className="w-44 shrink-0 truncate">{category.name}</span>
              <select
                aria-label={`Route for ${category.name}`}
                className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1"
                value={category.work_route_id ?? ""}
                onChange={(e) => set.mutate({ id: category.id, name: category.name, routeId: e.target.value === "" ? null : Number(e.target.value) })}
              >
                <option value="">Shop default{fallback ? ` (${fallback.name})` : ""}</option>
                {(routes ?? []).map((route) => (
                  <option key={route.id} value={route.id}>
                    {route.name}
                  </option>
                ))}
              </select>
            </li>
          ))}
        </ul>
      )}
      <ErrorBanner error={set.error} />
    </SettingsCard>
  );
}
