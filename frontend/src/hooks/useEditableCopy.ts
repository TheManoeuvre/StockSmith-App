import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { deepEqual } from "../lib/deepEqual";
import { useDirtyRegistration } from "./useDirtyRegistry";

/**
 * Holds an editor's local copy of server data, and knows whether it has been changed.
 *
 * These two jobs are deliberately in one hook, because they are the same state. Before this
 * existed, every editor mirrored its query into `useState` with an effect keyed on the query
 * result itself:
 *
 *     useEffect(() => { if (bom) setLines(bom.map(...)) }, [bom]);
 *
 * which re-seeds on EVERY refetch. Since `["products", id]` is invalidated by nearly every
 * save on the product page, editing a BOM while anything else saved silently discarded the
 * edit mid-typing. (BomOverrideEditor was the only editor that had spotted this and guarded
 * against it, with the seededKey ref this generalises.)
 *
 * So: seed once per `seedKey`, and never again. A later `seed` value under the SAME
 * `seedKey` is ignored — that is the whole fix. Changing `seedKey` (a different variant, a
 * different product) does re-seed, because that's a different thing being edited.
 *
 * The consequence is that "saved" can no longer be inferred from a re-seed, so `markSaved`
 * must be called explicitly in every mutation's onSuccess.
 */
export function useEditableCopy<T>({
  key,
  label,
  initial,
  seed,
  seedKey,
  project,
}: {
  /** Registry key, local to the enclosing <DirtyPath>. See useDirtyRegistry's path table. */
  key: string;
  /** Human-readable name for the unsaved-changes dialog, e.g. "Bill of Materials". */
  label: string;
  /** Value to hold before the first seed lands (an empty list, blank form fields, …). */
  initial: T;
  /** Server data mapped into editor shape; undefined while the query is still loading. */
  seed: T | undefined;
  /** Identity of the thing being edited. A change here re-seeds; a change to `seed` alone does not. */
  seedKey: string | number;
  /**
   * Narrows what counts as a change. Used where state is carried but not user-visible —
   * PricingSection keeps and sends platformFeePercent even when a calculated fee source hides
   * the input, and without this the editor would report itself dirty on a field nobody can see.
   */
  project?: (value: T) => unknown;
}): {
  value: T;
  setValue: Dispatch<SetStateAction<T>>;
  isDirty: boolean;
  isSeeded: boolean;
  markSaved: (canonical?: T) => void;
  /** Snaps the editor back to its last-saved baseline, discarding local edits (the footer
   *  Revert). Unlike `reseed` this restores the visible values immediately without waiting
   *  for a fresh `seed`. */
  revert: () => void;
  reseed: () => void;
} {
  const [value, setValue] = useState<T>(initial);
  const baselineRef = useRef<T>(initial);
  const seededKeyRef = useRef<string | number | null>(null);
  const [isSeeded, setIsSeeded] = useState(false);
  // Bumped whenever the baseline is replaced, so isDirty recomputes. baselineRef is a ref
  // precisely so a background refetch can't touch it — which means changes to it have to be
  // announced by hand.
  const [baselineVersion, setBaselineVersion] = useState(0);

  // Mirrors `value` for markSaved(), which must read the latest without re-creating itself
  // on every keystroke.
  const valueRef = useRef(value);
  valueRef.current = value;

  useEffect(() => {
    if (seed === undefined) return;
    if (seededKeyRef.current === seedKey) return; // already seeded for this entity — do NOT clobber
    seededKeyRef.current = seedKey;
    baselineRef.current = seed;
    setValue(seed);
    setIsSeeded(true);
    setBaselineVersion((v) => v + 1);
  }, [seed, seedKey]);

  const isDirty = useMemo(() => {
    if (!isSeeded) return false;
    const project_ = project ?? ((v: T) => v);
    return !deepEqual(project_(value), project_(baselineRef.current));
    // baselineVersion is a deliberate dependency: it is how a baseline swap re-triggers this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, project, isSeeded, baselineVersion]);

  /**
   * Clears the dirty flag. Pass the canonical server rows where the mutation returns them
   * (replaceBom / replaceKittingBom / replaceBundleItems all do) — otherwise the baseline is
   * what we SENT rather than what was STORED, and a server-side normalisation like
   * "1" -> "1.000" would leave the editor looking dirty the moment it re-renders.
   */
  const markSaved = useCallback((canonical?: T) => {
    baselineRef.current = canonical ?? valueRef.current;
    if (canonical !== undefined) setValue(canonical);
    setBaselineVersion((v) => v + 1);
  }, []);

  /** Discards local edits and lets the next `seed` take effect. */
  const reseed = useCallback(() => {
    seededKeyRef.current = null;
    setIsSeeded(false);
  }, []);

  /** Discards local edits, restoring the last-saved baseline right away. */
  const revert = useCallback(() => {
    setValue(baselineRef.current);
  }, []);

  useDirtyRegistration(key, label, isDirty);

  return { value, setValue, isDirty, isSeeded, markSaved, revert, reseed };
}
