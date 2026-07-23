import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import type { ShippingProfile } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

export function ShippingProfileSettings() {
  const queryClient = useQueryClient();
  const { data: profiles } = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    queryFn: shippingProfilesApi.list,
  });

  const [newName, setNewName] = useState("");
  const [newPrice, setNewPrice] = useState("");
  const [newCostEtsy, setNewCostEtsy] = useState("");
  const [newCostEbay, setNewCostEbay] = useState("");
  const [newCostManual, setNewCostManual] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["settings", "shipping-profiles"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
  };

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<ShippingProfile> }) =>
      shippingProfilesApi.update(id, input),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => shippingProfilesApi.delete(id),
    onSuccess: invalidate,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      shippingProfilesApi.create({
        name: newName,
        price: newPrice || null,
        cost_etsy: newCostEtsy || null,
        cost_ebay: newCostEbay || null,
        cost_manual: newCostManual || null,
      }),
    onSuccess: () => {
      invalidate();
      setNewName("");
      setNewPrice("");
      setNewCostEtsy("");
      setNewCostEbay("");
      setNewCostManual("");
    },
  });

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Shipping profiles</p>
        <p className="text-sm text-slate-500">
          Reusable shipping methods — Price is what you charge the customer (doesn't vary by channel). Cost is what
          it actually costs you, which can differ by where the label is bought — Etsy, eBay, or a manual order.
          Products default to one; orders snapshot the cost for their own channel when they ship, so historical
          profit doesn't drift.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse bg-white text-left text-xs shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-1.5">Name</th>
              <th className="p-1.5">Price charged</th>
              <th className="p-1.5">Cost (Etsy)</th>
              <th className="p-1.5">Cost (eBay)</th>
              <th className="p-1.5">Cost (Manual)</th>
              <th className="p-1.5" />
            </tr>
          </thead>
          <tbody>
            {profiles?.map((p) => (
              <ShippingProfileRow
                key={p.id}
                profile={p}
                onUpdate={(input) => updateMutation.mutate({ id: p.id, input })}
                onDelete={() => deleteMutation.mutate(p.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <ErrorBanner error={updateMutation.error ?? deleteMutation.error ?? createMutation.error} />
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          createMutation.mutate();
        }}
      >
        <input
          required
          placeholder="Profile name"
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <input
          placeholder="Price £"
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newPrice}
          onChange={(e) => setNewPrice(e.target.value)}
        />
        <input
          placeholder="Cost Etsy £"
          className="w-24 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newCostEtsy}
          onChange={(e) => setNewCostEtsy(e.target.value)}
        />
        <input
          placeholder="Cost eBay £"
          className="w-24 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newCostEbay}
          onChange={(e) => setNewCostEbay(e.target.value)}
        />
        <input
          placeholder="Cost Manual £"
          className="w-24 rounded border border-slate-300 px-2 py-1 text-sm"
          value={newCostManual}
          onChange={(e) => setNewCostManual(e.target.value)}
        />
        <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm">
          + Add profile
        </button>
      </form>
    </div>
  );
}

function ShippingProfileRow({
  profile,
  onUpdate,
  onDelete,
}: {
  profile: ShippingProfile;
  onUpdate: (input: Partial<ShippingProfile>) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(profile.name);
  const [price, setPrice] = useState(profile.price);
  const [costEtsy, setCostEtsy] = useState(profile.cost_etsy);
  const [costEbay, setCostEbay] = useState(profile.cost_ebay);
  const [costManual, setCostManual] = useState(profile.cost_manual);

  const dirty =
    name !== profile.name ||
    price !== profile.price ||
    costEtsy !== profile.cost_etsy ||
    costEbay !== profile.cost_ebay ||
    costManual !== profile.cost_manual;

  return (
    <tr className="border-b border-slate-100">
      <td className="p-1.5">
        <input
          className="rounded border border-slate-300 px-2 py-1"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </td>
      <td className="p-1.5">
        <input
          className="w-20 rounded border border-slate-300 px-2 py-1"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </td>
      <td className="p-1.5">
        <input
          className="w-20 rounded border border-slate-300 px-2 py-1"
          value={costEtsy}
          onChange={(e) => setCostEtsy(e.target.value)}
        />
      </td>
      <td className="p-1.5">
        <input
          className="w-20 rounded border border-slate-300 px-2 py-1"
          value={costEbay}
          onChange={(e) => setCostEbay(e.target.value)}
        />
      </td>
      <td className="p-1.5">
        <input
          className="w-20 rounded border border-slate-300 px-2 py-1"
          value={costManual}
          onChange={(e) => setCostManual(e.target.value)}
        />
      </td>
      <td className="p-1.5 flex gap-2">
        {dirty && (
          <button
            onClick={() =>
              onUpdate({ name, price, cost_etsy: costEtsy, cost_ebay: costEbay, cost_manual: costManual })
            }
            className="rounded bg-slate-900 px-2 py-1 text-white"
          >
            Save
          </button>
        )}
        <button onClick={onDelete} className="text-red-600">
          Remove
        </button>
      </td>
    </tr>
  );
}
