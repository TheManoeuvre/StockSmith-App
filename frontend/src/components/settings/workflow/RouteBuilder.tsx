import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { workApi, type WorkRoute, type WorkRouteStepInput, type WorkStage } from "../../../api/work";
import { useMaterialCategories } from "../../../hooks/useMaterialCategories";
import { ErrorBanner } from "../../common/ErrorBanner";
import { SettingsCard } from "../SettingsCard";

const inputClass = "rounded border border-slate-300 px-2 py-1 text-sm";

/**
 * Routes: the ordered steps a product goes through, from first preparation to Ship. Each route is
 * edited as a draft and saved as a whole, because an order only means something once the whole list
 * is valid — exactly one Make, and Ship last — and the server checks that on save.
 */
export function RouteBuilder() {
  const queryClient = useQueryClient();
  const { data: routes } = useQuery({ queryKey: ["work", "routes"], queryFn: workApi.routes });
  const { data: stages } = useQuery({ queryKey: ["work", "stages"], queryFn: workApi.stages });
  const [newName, setNewName] = useState("");

  const create = useMutation({
    mutationFn: () => {
      const make = stages?.find((s) => s.kind === "make");
      const ship = stages?.find((s) => s.kind === "ship");
      if (!make || !ship) throw new Error("The built-in Make and Ship stages are missing.");
      return workApi.createRoute(newName.trim(), [make, ship].map((s) => ({ stage_id: s.id, mix_variants: false, qty_basis: "unit", qty_category_id: null })));
    },
    onSuccess: () => {
      setNewName("");
      queryClient.invalidateQueries({ queryKey: ["work"] });
    },
  });

  return (
    <SettingsCard
      title="Routes"
      help="A route is the order of steps for a kind of product — for example Pre-clean → Mask → Make → Assemble → Ship. Make and Ship are required; Ship is always last."
    >
      <div className="flex flex-col gap-3">
        {(routes ?? []).map((route) => (
          <RouteEditor key={`${route.id}:${JSON.stringify(route.steps.map((s) => [s.stage_id, s.mix_variants, s.qty_basis, s.qty_category_id]))}:${route.name}:${route.is_default}`} route={route} stages={stages ?? []} />
        ))}
      </div>
      <form
        className="flex items-end gap-2 border-t border-[#f1f5f9] pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (newName.trim()) create.mutate();
        }}
      >
        <label className="flex flex-1 flex-col gap-1 text-[11.5px] text-slate-500">
          New route
          <input className={`${inputClass} w-full`} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Ceramic coaster" />
        </label>
        <button type="submit" disabled={!newName.trim() || create.isPending} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50">
          Add route
        </button>
      </form>
      <ErrorBanner error={create.error} />
    </SettingsCard>
  );
}

function toInputs(route: WorkRoute): WorkRouteStepInput[] {
  return route.steps.map((s) => ({ stage_id: s.stage_id, mix_variants: s.mix_variants, qty_basis: s.qty_basis, qty_category_id: s.qty_category_id }));
}

function RouteEditor({ route, stages }: { route: WorkRoute; stages: WorkStage[] }) {
  const queryClient = useQueryClient();
  const { categories } = useMaterialCategories();
  const [name, setName] = useState(route.name);
  const [steps, setSteps] = useState<WorkRouteStepInput[]>(() => toInputs(route));
  const [adding, setAdding] = useState("");
  const stageById = new Map(stages.map((s) => [s.id, s]));
  const dirty = name.trim() !== route.name || JSON.stringify(steps) !== JSON.stringify(toInputs(route));
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["work"] });

  const save = useMutation({ mutationFn: () => workApi.updateRoute(route.id, { name: name.trim(), steps }), onSuccess: refresh });
  const makeDefault = useMutation({ mutationFn: () => workApi.makeDefault(route.id), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => workApi.deleteRoute(route.id), onSuccess: refresh });

  const lastIndex = steps.length - 1;
  const move = (index: number, by: -1 | 1) => {
    const target = index + by;
    // Ship stays last, so nothing swaps with it.
    if (target < 0 || target >= lastIndex) return;
    const next = [...steps];
    [next[index], next[target]] = [next[target], next[index]];
    setSteps(next);
  };
  const patch = (index: number, change: Partial<WorkRouteStepInput>) => setSteps(steps.map((s, i) => (i === index ? { ...s, ...change } : s)));
  const available = stages.filter((s) => s.kind === "custom" && !steps.some((st) => st.stage_id === s.id));

  const usage = [
    route.category_count ? `${route.category_count} categor${route.category_count === 1 ? "y" : "ies"}` : null,
    route.product_count ? `${route.product_count} product${route.product_count === 1 ? "" : "s"}` : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label={`Name of route ${route.name}`} className={`${inputClass} min-w-[10rem] flex-1 font-medium`} value={name} onChange={(e) => setName(e.target.value)} />
        {route.is_default ? (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-[11.5px] text-slate-600">Shop default</span>
        ) : (
          <button type="button" className="text-[12px] text-slate-600 underline decoration-slate-300" onClick={() => makeDefault.mutate()}>
            Make default
          </button>
        )}
        {!route.is_default && (
          <button type="button" className="text-[12px] text-slate-500 underline decoration-slate-300 hover:text-red-700" onClick={() => remove.mutate()}>
            Delete route
          </button>
        )}
      </div>
      <p className="text-[11.5px] text-slate-500">{usage.length ? `Used by ${usage.join(" and ")}.` : route.is_default ? "Used by everything that doesn't choose a route." : "Not used yet."}</p>

      <ol className="flex flex-col gap-1.5">
        {steps.map((step, index) => {
          const stage = stageById.get(step.stage_id);
          if (!stage) return null;
          const isShip = stage.kind === "ship";
          return (
            <li key={step.stage_id} className="flex flex-wrap items-center gap-2 rounded bg-slate-50 px-2 py-1.5 text-sm">
              <span className="w-5 text-right text-[11.5px] tabular-nums text-slate-400">{index + 1}</span>
              <span className="min-w-[7rem] font-medium">{stage.name}</span>
              <span className="text-[11.5px] text-slate-500">{stage.station ?? ""}</span>
              <span className="flex-1" />
              {stage.kind === "custom" && (
                <label className="flex items-center gap-1 text-[12px] text-slate-600">
                  Counts
                  <select
                    aria-label={`What ${stage.name} counts`}
                    className="rounded border border-slate-300 px-1 py-0.5 text-[12px]"
                    value={step.qty_basis === "bom_category" ? String(step.qty_category_id ?? "") : "unit"}
                    onChange={(e) =>
                      e.target.value === "unit"
                        ? patch(index, { qty_basis: "unit", qty_category_id: null })
                        : patch(index, { qty_basis: "bom_category", qty_category_id: Number(e.target.value) })
                    }
                  >
                    <option value="unit">finished items</option>
                    {categories.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} in the recipe
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!isShip && (
                <label className="flex items-center gap-1 text-[12px] text-slate-600" title="Let one batch combine designs (variants) that print from the same materials. Off keeps each design separate, so they can't be mixed up afterwards.">
                  <input type="checkbox" aria-label={`Mix designs at ${stage.name}`} checked={step.mix_variants} onChange={(e) => patch(index, { mix_variants: e.target.checked })} />
                  Mix designs
                </label>
              )}
              {!isShip && (
                <span className="flex items-center gap-1">
                  <button type="button" aria-label={`Move ${stage.name} up`} disabled={index === 0} className="px-1 text-slate-500 disabled:opacity-30" onClick={() => move(index, -1)}>
                    ↑
                  </button>
                  <button type="button" aria-label={`Move ${stage.name} down`} disabled={index >= lastIndex - 1} className="px-1 text-slate-500 disabled:opacity-30" onClick={() => move(index, 1)}>
                    ↓
                  </button>
                </span>
              )}
              {stage.kind === "custom" && (
                <button type="button" aria-label={`Remove ${stage.name} from this route`} className="px-1 text-[12px] text-slate-500 hover:text-red-700" onClick={() => setSteps(steps.filter((_, i) => i !== index))}>
                  Remove
                </button>
              )}
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap items-center gap-2">
        <select aria-label={`Add a step to ${route.name}`} className={inputClass} value={adding} onChange={(e) => setAdding(e.target.value)}>
          <option value="">Add a step…</option>
          {available.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!adding}
          className="rounded border border-slate-300 px-2 py-1 text-sm disabled:opacity-50"
          onClick={() => {
            // New steps go in just before Make, i.e. as preparation; the arrows move them from there.
            const makeAt = steps.findIndex((s) => stageById.get(s.stage_id)?.kind === "make");
            const entry: WorkRouteStepInput = { stage_id: Number(adding), mix_variants: false, qty_basis: "unit", qty_category_id: null };
            setSteps([...steps.slice(0, makeAt), entry, ...steps.slice(makeAt)]);
            setAdding("");
          }}
        >
          Add
        </button>
        <span className="flex-1" />
        {dirty && (
          <button type="button" className="text-[12px] text-slate-600 underline decoration-slate-300" onClick={() => { setName(route.name); setSteps(toInputs(route)); }}>
            Discard changes
          </button>
        )}
        <button type="button" disabled={!dirty || save.isPending || !name.trim()} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50" onClick={() => save.mutate()}>
          Save route
        </button>
      </div>
      <ErrorBanner error={save.error ?? makeDefault.error ?? remove.error} />
    </div>
  );
}
