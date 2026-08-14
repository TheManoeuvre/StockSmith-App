import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformConfigApi, type PlatformFieldLimitRead } from "../../api/platformConfig";
import type { LimitField } from "../../api/platformLimits";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Lets a marketplace's field limits be corrected without waiting for a release.
 *
 * The numbers ship in code with a provenance comment on each — some are confirmed against
 * the marketplace's own schema, some are inferred from documentation nobody has tested.
 * When one turns out to be wrong on a packaged desktop build there is no other way to fix
 * it, so this exists.
 *
 * Collapsed by default: it is a repair tool, not a routine setting, and the defaults are
 * right almost always.
 */
export function PlatformLimitsEditor({ platform }: { platform: ListingPlatform }) {
  const [open, setOpen] = useState(false);
  const { data, error } = useQuery({
    queryKey: ["settings", "platform-limits", platform],
    queryFn: () => platformConfigApi.listLimits(platform),
    enabled: open,
  });

  const overrideCount = (data ?? []).filter((limit) => limit.is_override).length;

  return (
    <div className="rounded border border-slate-200 bg-white p-3 text-sm">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between text-left">
        <span>
          <span className="font-medium">{PLATFORM_LABELS[platform]} listing limits</span>
          {overrideCount > 0 && <span className="ml-2 text-xs text-amber-700">{overrideCount} overridden</span>}
        </span>
        <span className="text-slate-500">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <>
          <p className="mt-1 text-xs text-slate-500">
            StockSmith's built-in values are used unless you override one here. Overriding a single limit
            leaves the rest free to pick up corrections in future updates.
          </p>
          <ErrorBanner error={error} />
          <table className="mt-2 w-full text-left">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="p-1">Limit</th>
                <th className="p-1">Default</th>
                <th className="p-1">In use</th>
                <th className="p-1" />
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((limit) => (
                <LimitRow key={limit.field} platform={platform} limit={limit} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function LimitRow({ platform, limit }: { platform: ListingPlatform; limit: PlatformFieldLimitRead }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(limit.effective_value ?? "");
  const [note, setNote] = useState(limit.note ?? "");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["settings", "platform-limits", platform] });
    // A changed limit has to change what the compatibility report says immediately.
    queryClient.invalidateQueries({ queryKey: ["platforms", platform, "catalogue-compatibility"] });
  };

  const saveMutation = useMutation({
    mutationFn: (field: LimitField) =>
      platformConfigApi.setLimit(platform, field, {
        int_value: limit.kind === "int" ? Number(value) : null,
        text_value: limit.kind === "text" ? value : null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });

  const clearMutation = useMutation({
    mutationFn: (field: LimitField) => platformConfigApi.clearLimit(platform, field),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });

  return (
    <>
      <tr className="border-b border-slate-100">
        <td className="p-1">{limit.label}</td>
        <td className="p-1 font-mono text-xs text-slate-500">{limit.default_value}</td>
        <td className={`p-1 font-mono text-xs ${limit.is_override ? "font-medium text-amber-800" : ""}`}>
          {limit.effective_value}
        </td>
        <td className="p-1 text-right">
          <button onClick={() => setEditing((v) => !v)} className="text-xs text-slate-600 underline">
            {editing ? "Cancel" : "Change"}
          </button>
          {limit.is_override && (
            <button
              onClick={() => clearMutation.mutate(limit.field)}
              className="ml-2 text-xs text-slate-600 underline"
            >
              Reset
            </button>
          )}
        </td>
      </tr>
      {editing && (
        <tr className="border-b border-slate-100 bg-slate-50">
          <td colSpan={4} className="p-2">
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs">
                <span>New value</span>
                <input
                  className="rounded border border-slate-300 px-2 py-1"
                  type={limit.kind === "int" ? "number" : "text"}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              <label className="flex flex-1 flex-col gap-1 text-xs">
                {/* A number someone changed eighteen months ago with no note is
                    indistinguishable from a mistake. */}
                <span>Why (optional)</span>
                <input
                  className="w-full rounded border border-slate-300 px-2 py-1"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. confirmed accepted on a live listing"
                />
              </label>
              <button
                onClick={() => saveMutation.mutate(limit.field)}
                disabled={saveMutation.isPending || value.trim() === ""}
                className="rounded border border-slate-400 px-3 py-1 text-xs disabled:opacity-50"
              >
                Save
              </button>
            </div>
            <ErrorBanner error={saveMutation.error} />
          </td>
        </tr>
      )}
    </>
  );
}
