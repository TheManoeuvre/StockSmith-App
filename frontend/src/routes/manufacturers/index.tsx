import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { manufacturersApi } from "../../api/manufacturers";
import { ErrorBanner } from "../../components/common/ErrorBanner";

export const Route = createFileRoute("/manufacturers/")({
  component: ManufacturersList,
});

function ManufacturersList() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["manufacturers"], queryFn: manufacturersApi.list });
  const [name, setName] = useState("");

  const createMutation = useMutation({
    mutationFn: () => manufacturersApi.findOrCreate(name.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["manufacturers"] });
      setName("");
    },
  });

  if (isLoading) return <p>Loading manufacturers…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Manufacturers</h1>

      <form
        className="flex items-end gap-2 rounded bg-white p-4 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) createMutation.mutate();
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm">Name</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
          Add
        </button>
      </form>
      <ErrorBanner error={createMutation.error} />

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Name</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((m) => (
            <tr key={m.id} className="border-b border-slate-100">
              <td className="p-2">{m.name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
