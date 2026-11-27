import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { workApi, type WorkStage } from "../../../api/work";
import { ErrorBanner } from "../../common/ErrorBanner";
import { SettingsCard } from "../SettingsCard";

const inputClass = "w-full rounded border border-slate-300 px-2 py-1 text-sm";

/**
 * The steps you can put in a route. Make and Ship are built in — Make is where a build is recorded
 * and Ship is the order flow — so they can be renamed but not removed. Edits save as you leave a
 * field, since each is one small independent change.
 */
export function StageLibrary() {
  const queryClient = useQueryClient();
  const { data: stages } = useQuery({ queryKey: ["work", "stages"], queryFn: workApi.stages });
  const [name, setName] = useState("");
  const [station, setStation] = useState("");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["work"] });
  };
  const create = useMutation({
    mutationFn: () => workApi.createStage({ name: name.trim(), station: station.trim() || null }),
    onSuccess: () => {
      setName("");
      setStation("");
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, input }: { id: number; input: Parameters<typeof workApi.updateStage>[1] }) => workApi.updateStage(id, input),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: number) => workApi.deleteStage(id), onSuccess: refresh });

  const stations = [...new Set((stages ?? []).map((s) => s.station).filter((s): s is string => !!s))];

  return (
    <SettingsCard
      title="Stages"
      help="The steps in your process — pre-clean, mask, sand, assemble. A station is where you do it (desk, bench, printers); the Work page groups tasks by station so you aren't moving about."
    >
      <datalist id="work-stations">
        {stations.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-[11.5px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-1 pr-2 font-medium">Name</th>
              <th className="py-1 pr-2 font-medium">Station</th>
              <th className="py-1 pr-2 font-medium" title="Work that only makes sense for one buyer, like making a personalised file. It is never prepared ahead of an order.">
                Per order
              </th>
              <th className="py-1 pr-2 font-medium" title="Minutes to wait (drying, curing) before the next step.">
                Wait (min)
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(stages ?? []).map((stage) => (
              <StageRow key={`${stage.id}:${stage.name}:${stage.station}:${stage.wait_minutes}`} stage={stage} onUpdate={(input) => update.mutate({ id: stage.id, input })} onDelete={() => remove.mutate(stage.id)} />
            ))}
          </tbody>
        </table>
      </div>
      <ErrorBanner error={update.error ?? remove.error} />
      <form
        className="flex flex-wrap items-end gap-2 border-t border-[#f1f5f9] pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <label className="flex min-w-[10rem] flex-1 flex-col gap-1 text-[11.5px] text-slate-500">
          New stage
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="Sand, Mask, Assemble…" />
        </label>
        <label className="flex min-w-[8rem] flex-1 flex-col gap-1 text-[11.5px] text-slate-500">
          Station
          <input className={inputClass} list="work-stations" value={station} onChange={(e) => setStation(e.target.value)} placeholder="Bench" />
        </label>
        <button type="submit" disabled={!name.trim() || create.isPending} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50">
          Add stage
        </button>
      </form>
      <ErrorBanner error={create.error} />
    </SettingsCard>
  );
}

function StageRow({
  stage,
  onUpdate,
  onDelete,
}: {
  stage: WorkStage;
  onUpdate: (input: Parameters<typeof workApi.updateStage>[1]) => void;
  onDelete: () => void;
}) {
  const builtIn = stage.kind !== "custom";
  const [name, setName] = useState(stage.name);
  const [station, setStation] = useState(stage.station ?? "");
  const [wait, setWait] = useState(stage.wait_minutes === null ? "" : String(stage.wait_minutes));
  return (
    <tr className="border-t border-[#f1f5f9] align-middle">
      <td className="py-1.5 pr-2">
        <input
          aria-label={`Name of ${stage.name}`}
          className={inputClass}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name.trim() !== stage.name && onUpdate({ name: name.trim() })}
        />
        {builtIn && <span className="text-[11px] text-slate-500">Built in — {stage.kind === "make" ? "records a build" : "the order flow"}</span>}
      </td>
      <td className="py-1.5 pr-2">
        <input
          aria-label={`Station for ${stage.name}`}
          className={inputClass}
          list="work-stations"
          value={station}
          onChange={(e) => setStation(e.target.value)}
          onBlur={() => station.trim() !== (stage.station ?? "") && onUpdate({ station: station.trim() || null })}
        />
      </td>
      <td className="py-1.5 pr-2">
        <input
          type="checkbox"
          aria-label={`${stage.name} is per order`}
          checked={stage.per_order}
          disabled={builtIn}
          onChange={(e) => onUpdate({ per_order: e.target.checked })}
        />
      </td>
      <td className="py-1.5 pr-2">
        <input
          aria-label={`Wait minutes for ${stage.name}`}
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          inputMode="numeric"
          disabled={builtIn}
          value={wait}
          onChange={(e) => setWait(e.target.value.replace(/\D/g, ""))}
          onBlur={() => {
            const next = wait === "" ? null : Number(wait);
            if (next !== stage.wait_minutes) onUpdate({ wait_minutes: next });
          }}
        />
      </td>
      <td className="py-1.5 text-right">
        {!builtIn && (
          <button
            type="button"
            className="text-[12px] text-slate-500 underline decoration-slate-300 hover:text-red-700"
            onClick={onDelete}
            title={stage.route_count ? `Used by ${stage.route_count} route${stage.route_count === 1 ? "" : "s"}` : undefined}
          >
            Delete
          </button>
        )}
      </td>
    </tr>
  );
}
