import { useBlocker } from "@tanstack/react-router";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { useDirtyRegistryApi } from "./useDirtyRegistry";

export interface GuardAttemptOptions {
  /** Only consider editors under this registry path prefix (e.g. `variant-3/`). */
  prefix?: string;
  /** Several disjoint subtrees at once — "Show less" hides a whole set of rows. */
  prefixes?: string[];
}

export interface UnsavedChangesGuard {
  /**
   * Runs `action` immediately if nothing relevant is dirty; otherwise asks first and runs it
   * only if the user chooses to discard.
   */
  attempt: (action: () => void, opts?: GuardAttemptOptions) => void;
  dialogProps: { open: boolean; labels: string[]; onDiscard: () => void; onCancel: () => void };
}

/**
 * One confirmation for two quite different situations.
 *
 * Router navigation is REACTIVE: the router has already stopped and hands us proceed/reset,
 * so we respond after the fact. Every other way of losing edits on this page — switching the
 * expanded variant, "Show less", changing the pricing mode, ticking "This is a bundle" — is
 * PRE-EMPTIVE: the unmount is always caused by a state setter in an ancestor we control. A
 * React unmount can't be cancelled once it starts, so we never try; we veto the setter that
 * would have caused it. `attempt` is that veto.
 *
 * Both funnel into the same dialog, so it doesn't matter to the user which kind it was.
 */
export function useUnsavedChangesGuard(): UnsavedChangesGuard {
  const registry = useDirtyRegistryApi();
  const pendingActionRef = useRef<(() => void) | null>(null);
  const [labels, setLabels] = useState<string[]>([]);
  const [pendingOpen, setPendingOpen] = useState(false);

  const blocker = useBlocker({
    shouldBlockFn: () => registry.isDirtyUnder(""),
    enableBeforeUnload: () => registry.isDirtyUnder(""),
    withResolver: true,
  });

  const attempt = useCallback(
    (action: () => void, opts?: GuardAttemptOptions) => {
      const prefixes = opts?.prefixes ?? [opts?.prefix ?? ""];
      const dirty = prefixes.some((p) => registry.isDirtyUnder(p));
      if (!dirty) {
        action();
        return;
      }
      pendingActionRef.current = action;
      setLabels(prefixes.flatMap((p) => registry.dirtyLabelsUnder(p)));
      setPendingOpen(true);
    },
    [registry]
  );

  const blocked = blocker.status === "blocked";

  const onDiscard = useCallback(() => {
    const action = pendingActionRef.current;
    pendingActionRef.current = null;
    setPendingOpen(false);
    if (action) action();
    else if (blocked) blocker.proceed();
  }, [blocked, blocker]);

  const onCancel = useCallback(() => {
    pendingActionRef.current = null;
    setPendingOpen(false);
    if (blocked) blocker.reset();
  }, [blocked, blocker]);

  return {
    attempt,
    dialogProps: {
      open: pendingOpen || blocked,
      // A router block has no prefix to narrow by, so it names everything unsaved.
      labels: pendingOpen ? labels : registry.dirtyLabelsUnder(""),
      onDiscard,
      onCancel,
    },
  };
}

// Passing the guard down as props would mean threading it through VariantEditor -> VariantRow
// -> BomOverrideEditor and PricingSection -> LinePriceTable -> LineRow purely to reach a
// couple of click handlers. Context instead. Defaults to running the action unguarded so a
// component still works when mounted with no provider above it — which in practice means unit
// tests, since the app wires GuardProvider in at the root (see routes/__root.tsx).
const GuardContext = createContext<UnsavedChangesGuard>({
  attempt: (action) => action(),
  dialogProps: { open: false, labels: [], onDiscard: () => {}, onCancel: () => {} },
});

export function GuardProvider({ guard, children }: { guard: UnsavedChangesGuard; children: ReactNode }) {
  return <GuardContext.Provider value={guard}>{children}</GuardContext.Provider>;
}

export function useGuard(): UnsavedChangesGuard {
  return useContext(GuardContext);
}
