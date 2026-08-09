import { createContext, useContext, useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from "react";

/**
 * Tracks which editors on a page have unsaved changes, so an ancestor can ask "is anything
 * unsaved below me?" before doing something that would destroy those edits.
 *
 * A single flat map keyed by hierarchical path strings, rather than nested per-subtree
 * contexts. Every place that needs to veto — the variant accordion collapsing a row, "Show
 * less" unmounting rows past a limit, the pricing-mode select swapping out the whole form —
 * is an ANCESTOR asking about a DESCENDANT subtree, imperatively, at click time. Nested
 * providers can't answer that without ref-forwarding gymnastics; a flat map with prefix
 * queries answers it in one line.
 *
 * ## Path allocation (the contract — keep this list current)
 *
 *   details                                  Details form
 *   bom / kitting-bom / bundle-items         the Bill of Materials tab's editors
 *   variant-attributes                       Generate variants form
 *   stock/build, stock/adjust                Stock tab's two command forms
 *   assets/${assetType}                      Asset URL import fields
 *   pricing/product                          product-mode price form
 *   pricing/group-${attributeValue}          variable-mode group forms
 *   pricing/line-${variantId}/price          line-mode per-variant forms
 *   variant-${id}/rename                     variant name + SKU suffix
 *   variant-${id}/bom-overrides              build BOM overrides
 *   variant-${id}/kitting-bom-overrides      kitting BOM overrides
 *
 * Paths are built by nesting <DirtyPath segment="..."> around a subtree; each editor then
 * registers a short local key. Prefix queries use the trailing slash, so `variant-1/` never
 * matches `variant-12/`.
 */

interface DirtyEntry {
  label: string;
  dirty: boolean;
}

export class DirtyRegistry {
  private entries = new Map<string, DirtyEntry>();
  private listeners = new Set<() => void>();
  private snapshot: string[] = [];

  set(path: string, label: string, dirty: boolean): void {
    const existing = this.entries.get(path);
    if (existing && existing.label === label && existing.dirty === dirty) return;
    this.entries.set(path, { label, dirty });
    this.publish();
  }

  remove(path: string): void {
    if (this.entries.delete(path)) this.publish();
  }

  isDirtyUnder(prefix = ""): boolean {
    for (const [path, entry] of this.entries) {
      if (entry.dirty && path.startsWith(prefix)) return true;
    }
    return false;
  }

  /** Human-readable names of the dirty editors under `prefix`, for the confirmation dialog. */
  dirtyLabelsUnder(prefix = ""): string[] {
    const labels: string[] = [];
    for (const [path, entry] of this.entries) {
      if (entry.dirty && path.startsWith(prefix) && !labels.includes(entry.label)) labels.push(entry.label);
    }
    return labels;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /**
   * useSyncExternalStore requires a referentially STABLE result when nothing has changed —
   * returning a fresh array every call is an infinite render loop. So the snapshot is cached
   * and only swapped when the set of dirty paths actually differs.
   */
  getSnapshot = (): string[] => this.snapshot;

  private publish(): void {
    const next: string[] = [];
    for (const [path, entry] of this.entries) {
      if (entry.dirty) next.push(path);
    }
    next.sort();
    const unchanged = next.length === this.snapshot.length && next.every((p, i) => p === this.snapshot[i]);
    if (unchanged) return;
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }
}

// Default is a real (shared, never-written-to) registry rather than null, so
// useDirtyRegistration is inert outside a provider. That keeps components built on
// useEditableCopy renderable standalone — unit tests mount them bare, with no provider and no
// router. The app itself always has one: DirtyRegistryProvider wraps everything at the root
// (see routes/__root.tsx), so every route is covered, settings included.
const NOOP_REGISTRY = new DirtyRegistry();

const RegistryContext = createContext<DirtyRegistry>(NOOP_REGISTRY);
const PathContext = createContext<string>("");

export function DirtyRegistryProvider({ children }: { children: ReactNode }) {
  const registry = useMemo(() => new DirtyRegistry(), []);
  return <RegistryContext.Provider value={registry}>{children}</RegistryContext.Provider>;
}

/** Nests a path segment for everything rendered inside — see the path table above. */
export function DirtyPath({ segment, children }: { segment: string; children: ReactNode }) {
  const parent = useContext(PathContext);
  const path = `${parent}${segment}/`;
  return <PathContext.Provider value={path}>{children}</PathContext.Provider>;
}

export function useDirtyRegistration(localKey: string, label: string, dirty: boolean): void {
  const registry = useContext(RegistryContext);
  const parent = useContext(PathContext);
  const path = `${parent}${localKey}`;

  // Two effects on purpose. The first re-runs whenever `dirty` flips; the second must only
  // run its cleanup when the component actually unmounts (or moves path), because
  // unregistering on every dirty flip would briefly report "nothing unsaved" mid-edit.
  const registryRef = useRef(registry);
  registryRef.current = registry;

  useEffect(() => {
    registry.set(path, label, dirty);
  }, [registry, path, label, dirty]);

  useEffect(() => {
    return () => registryRef.current.remove(path);
  }, [path]);
}

/** Imperative, non-subscribing access — for veto sites that only read on click. */
export function useDirtyRegistryApi(): DirtyRegistry {
  return useContext(RegistryContext);
}

/** Subscribing read, for UI that must re-render as dirtiness changes. */
export function useAnyDirty(prefix = ""): { isDirty: boolean; labels: string[] } {
  const registry = useContext(RegistryContext);
  useSyncExternalStore(registry.subscribe, registry.getSnapshot, registry.getSnapshot);
  return { isDirty: registry.isDirtyUnder(prefix), labels: registry.dirtyLabelsUnder(prefix) };
}
