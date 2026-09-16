import { SaveButton } from "stocksmith-ui";

/** Nothing to save: disabled IS the "all saved" signal. */
export const Clean = () => (
  <div className="flex items-center gap-3">
    <SaveButton isDirty={false} isPending={false} status="idle">Save</SaveButton>
  </div>
);

export const Dirty = () => (
  <div className="flex items-center gap-3">
    <SaveButton isDirty isPending={false} status="idle">Save</SaveButton>
  </div>
);

export const Pending = () => (
  <div className="flex items-center gap-3">
    <SaveButton isDirty isPending status="saving">Save</SaveButton>
  </div>
);

export const JustSaved = () => (
  <div className="flex items-center gap-3">
    <SaveButton isDirty={false} isPending={false} status="saved">Save</SaveButton>
  </div>
);

/** A command form: enabledWhen overrides the dirty check. */
export const CommandForm = () => (
  <div className="flex items-center gap-3">
    <input type="number" defaultValue={1} className="w-20 rounded border border-slate-300 px-2 py-1 text-sm" />
    <SaveButton isDirty={false} isPending={false} status="idle" enabledWhen>Record build</SaveButton>
  </div>
);
