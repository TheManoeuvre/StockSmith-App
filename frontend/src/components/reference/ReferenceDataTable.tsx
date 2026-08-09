import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { DirtyPath } from "../../hooks/useDirtyRegistry";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { ApiError } from "../../api/client";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";

export interface ReferenceRow {
  id: number;
  name: string;
  usage_count: number;
}

/** Fields are addressed by string key, which the concrete row types don't declare. */
function fieldValue(row: ReferenceRow, key: string): string {
  const value = (row as unknown as Record<string, unknown>)[key];
  return value == null ? "" : String(value);
}

export interface ReferenceField {
  key: string;
  label: string;
  type?: "text" | "url" | "money";
  placeholder?: string;
}

export interface ReferenceDataApi<T extends ReferenceRow> {
  list: () => Promise<T[]>;
  create: (name: string) => Promise<T>;
  update: (id: number, input: Record<string, unknown>) => Promise<T>;
  remove?: (id: number) => Promise<void>;
  merge?: (id: number, targetId: number) => Promise<T>;
}

/**
 * A reference-data list you can edit in place.
 *
 * Replaces click-through-to-a-detail-page (which for these three tables didn't even exist —
 * they were read-only) with an accordion: expand a row, edit it, save, move to the next. The
 * point is that comparing and correcting several entries doesn't cost a navigation each time.
 *
 * Unsaved-changes handling is entirely inherited. Each expanded row registers through
 * useEditableCopy under `<segment>/row-<id>`, so the root blocker covers navigating away and
 * switching settings tabs with no code here. The one thing this component must do itself is
 * veto its *own* collapse — an unmount can't be cancelled after the fact, so the guard has to
 * be asked before the state changes. Same pattern, and the same reason, as VariantEditor.
 */
export function ReferenceDataTable<T extends ReferenceRow>({
  title,
  description,
  segment,
  queryKey,
  api,
  fields,
  usageLabel,
  allowDelete = true,
  extraRowActions,
}: {
  title: string;
  description?: string;
  /** DirtyPath segment, e.g. "manufacturers". Must be unique within the page. */
  segment: string;
  queryKey: QueryKey;
  api: ReferenceDataApi<T>;
  fields: ReferenceField[];
  /** "12 materials" — reads differently per resource, so the caller words it. */
  usageLabel: (count: number) => string;
  allowDelete?: boolean;
  extraRowActions?: (row: T) => React.ReactNode;
}) {
  const { data: rows } = useQuery({ queryKey, queryFn: api.list });
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const guard = useGuard();

  const toggle = (id: number) => {
    const next = expandedId === id ? null : id;
    if (expandedId === null) {
      setExpandedId(next);
      return;
    }
    // The prefix is the row about to unmount, not the one being clicked.
    guard.attempt(() => setExpandedId(next), { prefix: `${segment}/row-${expandedId}/` });
  };

  return (
    <DirtyPath segment={segment}>
      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <div>
          <h2 className="font-medium">{title}</h2>
          {description && <p className="text-sm text-slate-500">{description}</p>}
        </div>

        <CreateForm title={title} api={api} queryKey={queryKey} />

        {rows && rows.length === 0 && <p className="text-sm text-slate-500">Nothing here yet.</p>}

        {rows && rows.length > 0 && (
          <ul className="flex flex-col divide-y divide-slate-100 rounded border border-slate-200 bg-white">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  aria-expanded={expandedId === row.id}
                  onClick={() => toggle(row.id)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-slate-50"
                >
                  <span className="font-medium">{row.name}</span>
                  <span className="text-xs text-slate-400">
                    {row.usage_count > 0 ? usageLabel(row.usage_count) : "unused"}
                  </span>
                </button>
                {expandedId === row.id && (
                  <DirtyPath segment={`row-${row.id}`}>
                    <ExpandedRow
                      row={row}
                      rows={rows}
                      title={title}
                      fields={fields}
                      api={api}
                      queryKey={queryKey}
                      allowDelete={allowDelete}
                      usageLabel={usageLabel}
                      extraRowActions={extraRowActions}
                      onDone={() => setExpandedId(null)}
                    />
                  </DirtyPath>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </DirtyPath>
  );
}

function CreateForm<T extends ReferenceRow>({
  title,
  api,
  queryKey,
}: {
  title: string;
  api: ReferenceDataApi<T>;
  queryKey: QueryKey;
}) {
  const queryClient = useQueryClient();
  // A command form: registered so half-typed input isn't silently dropped on navigate, but
  // gated on having a name rather than on being dirty.
  const {
    value: draft,
    setValue: setDraft,
    isDirty,
    markSaved,
  } = useEditableCopy<{ name: string }>({
    key: "new",
    label: `New ${title.toLowerCase().replace(/s$/, "")}`,
    initial: { name: "" },
    seed: { name: "" },
    seedKey: "const",
  });

  const createMutation = useMutation({
    mutationFn: () => api.create(draft.name.trim()),
    onSuccess: () => {
      markSaved({ name: "" });
      queryClient.invalidateQueries({ queryKey });
    },
  });
  const status = useSaveStatus(createMutation.status);

  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.name.trim()) createMutation.mutate();
      }}
    >
      <label className="flex flex-col gap-1">
        <span className="text-sm">Add {title.toLowerCase().replace(/s$/, "")}</span>
        <input
          aria-label={`New ${title.toLowerCase().replace(/s$/, "")} name`}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={draft.name}
          onChange={(e) => setDraft({ name: e.target.value })}
        />
      </label>
      <SaveButton
        type="submit"
        isDirty={isDirty}
        isPending={createMutation.isPending}
        status={status}
        enabledWhen={!!draft.name.trim()}
        className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
      >
        Add
      </SaveButton>
      <ErrorBanner error={createMutation.error} />
    </form>
  );
}

function ExpandedRow<T extends ReferenceRow>({
  row,
  rows,
  title,
  fields,
  api,
  queryKey,
  allowDelete,
  usageLabel,
  extraRowActions,
  onDone,
}: {
  row: T;
  rows: T[];
  title: string;
  fields: ReferenceField[];
  api: ReferenceDataApi<T>;
  queryKey: QueryKey;
  allowDelete: boolean;
  usageLabel: (count: number) => string;
  extraRowActions?: (row: T) => React.ReactNode;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [mergeTargetId, setMergeTargetId] = useState<number | null>(null);
  const [confirmingMerge, setConfirmingMerge] = useState(false);

  const seed = useMemo(
    () => Object.fromEntries(fields.map((f) => [f.key, fieldValue(row, f.key)])),
    [row, fields]
  );

  const {
    value: form,
    setValue: setForm,
    isDirty,
    markSaved,
  } = useEditableCopy<Record<string, string>>({
    key: "fields",
    label: `${title} — ${row.name}`,
    initial: seed,
    seed,
    // The row id: a different row is a different thing being edited, but a background refetch
    // of the same row must not clobber what's being typed.
    seedKey: row.id,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey });
    // Names are read through relationships, so anything displaying one is now stale.
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    queryClient.invalidateQueries({ queryKey: ["purchases"] });
  };

  const saveMutation = useMutation({
    mutationFn: () => api.update(row.id, form),
    onSuccess: (saved) => {
      // Baseline from what was stored, not what was sent — the server trims names, and a
      // trimmed value coming back would otherwise leave the row looking dirty immediately.
      markSaved(Object.fromEntries(fields.map((f) => [f.key, fieldValue(saved, f.key)])));
      invalidate();
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  const deleteMutation = useMutation({
    mutationFn: () => api.remove!(row.id),
    onSuccess: () => {
      setConfirmingDelete(false);
      invalidate();
      onDone();
    },
  });

  const mergeMutation = useMutation({
    mutationFn: (targetId: number) => api.merge!(row.id, targetId),
    onSuccess: () => {
      setConfirmingMerge(false);
      invalidate();
      onDone();
    },
  });

  // A 409 on save means the name is taken. The list is already loaded, so the clashing row can
  // be found locally — no need for the server to return a structured error.
  const conflictName =
    saveMutation.error instanceof ApiError && saveMutation.error.status === 409 ? form.name.trim() : null;
  const conflictRow = conflictName ? rows.find((r) => r.name === conflictName && r.id !== row.id) : undefined;

  const others = rows.filter((r) => r.id !== row.id);
  const mergeTarget = others.find((r) => r.id === mergeTargetId);

  return (
    <div className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50 px-3 py-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <label key={field.key} className="flex flex-col gap-1 text-sm">
            {field.label}
            <input
              aria-label={`${row.name} ${field.label}`}
              type={field.type === "money" ? "number" : "text"}
              step={field.type === "money" ? "0.01" : undefined}
              placeholder={field.placeholder}
              className="rounded border border-slate-300 px-2 py-1"
              value={form[field.key] ?? ""}
              onChange={(e) => setForm((prev) => ({ ...prev, [field.key]: e.target.value }))}
            />
          </label>
        ))}
      </div>

      {conflictRow && api.merge && (
        <div className="flex flex-wrap items-center gap-2 rounded bg-amber-50 p-2 text-sm text-amber-900">
          <span>Another entry is already called "{conflictRow.name}".</span>
          <button
            type="button"
            onClick={() => {
              setMergeTargetId(conflictRow.id);
              setConfirmingMerge(true);
            }}
            className="underline"
          >
            Merge into it
          </button>
        </div>
      )}
      {!conflictRow && <ErrorBanner error={saveMutation.error} />}

      <div className="flex flex-wrap items-center gap-2">
        <SaveButton
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          onClick={() => saveMutation.mutate()}
        >
          Save
        </SaveButton>

        {api.merge && others.length > 0 && (
          <div className="flex items-center gap-1 text-sm">
            <label className="text-slate-500" htmlFor={`merge-${row.id}`}>
              Merge into
            </label>
            <select
              id={`merge-${row.id}`}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
              value={mergeTargetId ?? ""}
              onChange={(e) => setMergeTargetId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">Choose…</option>
              {others.map((other) => (
                <option key={other.id} value={other.id}>
                  {other.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={mergeTargetId === null}
              onClick={() => setConfirmingMerge(true)}
              className="rounded border border-slate-300 px-2 py-1 disabled:opacity-50"
            >
              Merge
            </button>
          </div>
        )}

        {extraRowActions?.(row)}

        {allowDelete && api.remove && (
          <button
            type="button"
            disabled={row.usage_count > 0}
            title={row.usage_count > 0 ? `Used by ${usageLabel(row.usage_count)}` : undefined}
            onClick={() => setConfirmingDelete(true)}
            className="ml-auto text-sm text-red-600 disabled:cursor-not-allowed disabled:text-slate-400"
          >
            {row.usage_count > 0 ? `In use by ${usageLabel(row.usage_count)}` : "Delete"}
          </button>
        )}
      </div>
      <ErrorBanner error={deleteMutation.error ?? mergeMutation.error} />

      <ConfirmDialog
        open={confirmingDelete}
        title={`Delete "${row.name}"?`}
        confirmLabel="Delete"
        busy={deleteMutation.isPending}
        // No typed gate: an unused reference row is about as low-stakes as a destructive action
        // gets, and reserving that friction for restore keeps it meaningful.
        body={<p>Nothing references it, so nothing else changes.</p>}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setConfirmingDelete(false)}
      />

      <ConfirmDialog
        open={confirmingMerge && mergeTarget !== undefined}
        title="Merge these entries?"
        confirmLabel="Merge"
        busy={mergeMutation.isPending}
        body={
          mergeTarget && (
            <p>
              Everything using <span className="font-medium">{row.name}</span>
              {row.usage_count > 0 && <> ({usageLabel(row.usage_count)})</>} will be changed to use{" "}
              <span className="font-medium">{mergeTarget.name}</span>, and{" "}
              <span className="font-medium">{row.name}</span> will be deleted. This can't be undone.
            </p>
          )
        }
        onConfirm={() => mergeTargetId !== null && mergeMutation.mutate(mergeTargetId)}
        onCancel={() => setConfirmingMerge(false)}
      />
    </div>
  );
}
