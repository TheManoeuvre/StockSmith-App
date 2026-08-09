import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * One reference-data list, rendered inline under Settings → Reference data.
 *
 * Replaces three near-identical route files (/manufacturers, /suppliers, /material-types) that
 * were copy-pasted from each other and only ever displayed a name column. Behaviour is
 * unchanged for now — list, and add via find-or-create.
 *
 * Renaming, deleting and merging are the obvious next things and are deliberately not here yet:
 * the backend has no PATCH or DELETE for any of these resources, so they need endpoints before
 * they need UI. Worth knowing when that lands: these are already real foreign keys
 * (materials.manufacturer_id, purchases.supplier_id, materials.material_type_id), and the name
 * is only ever read through a relationship — never copied onto the referencing row. So a rename
 * cascades everywhere by construction; it needs no data migration and no fan-out update.
 */
export function ReferenceDataSection<T extends { id: number; name: string }>({
  title,
  description,
  queryKey,
  list,
  findOrCreate,
}: {
  title: string;
  description?: string;
  queryKey: readonly unknown[];
  list: () => Promise<T[]>;
  findOrCreate: (name: string) => Promise<T>;
}) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey, queryFn: list });
  const [name, setName] = useState("");

  const createMutation = useMutation({
    mutationFn: () => findOrCreate(name.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      setName("");
    },
  });

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <h2 className="font-medium">{title}</h2>
        {description && <p className="text-sm text-slate-500">{description}</p>}
      </div>

      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) createMutation.mutate();
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm">Name</span>
          <input
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button type="submit" className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          Add
        </button>
      </form>
      <ErrorBanner error={createMutation.error} />

      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {error && <p className="text-sm text-red-600">{(error as Error).message}</p>}
      {data && data.length === 0 && <p className="text-sm text-slate-500">Nothing here yet.</p>}
      {data && data.length > 0 && (
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Name</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.id} className="border-b border-slate-100">
                <td className="p-2">{row.name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
