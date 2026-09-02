import { useEffect, useId, type ReactNode } from "react";
import { Tabs, type TabDef } from "./Tabs";

/**
 * The slide-over shell every list+detail pair (Products, Materials, Orders, Purchases,
 * Stock Take) mounts its detail view into.
 *
 * The list route stays mounted underneath — this only covers the overlay chrome (backdrop,
 * panel, header with close/prev/next, optional tab bar). Each entity's own `$entityId` route
 * supplies `title`/`tabs`/`children`; the pathless layout route for that entity supplies
 * `onClose`/`onPrev`/`onNext` since those need the list's already-fetched id sequence.
 */
export function DetailPanel({
  title,
  onClose,
  onPrev,
  onNext,
  tabs,
  activeTab,
  onTabChange,
  headerExtra,
  footer,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  tabs?: TabDef[];
  activeTab?: string;
  onTabChange?: (id: string) => void;
  /** Extra controls in the header row, e.g. an active/inactive badge. */
  headerExtra?: ReactNode;
  /** A persistent bar below the scroll area, e.g. the product slide-over's Save/Revert. */
  footer?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/20" onClick={(e) => e.target === e.currentTarget && onClose()}>
      {/* aria-labelledby (not a plain aria-label) so this stays correctly named even though
          `title` is a ReactNode rather than a string — and so it has a distinct accessible
          name from any Modal-based dialog (ConfirmDialog, UnsavedChangesDialog) that opens on
          top of it, which also uses role="dialog"; without a name the two are indistinguishable
          to both screen readers and role-based test queries. */}
      <div role="dialog" aria-modal="true" aria-labelledby={headingId} className="flex h-full w-full max-w-3xl flex-col bg-white shadow-xl">
        <div className="flex flex-none items-center gap-2 border-b border-slate-200 p-4">
          <h1 id={headingId} className="flex-1 truncate text-lg font-semibold">{title}</h1>
          {headerExtra}
          {(onPrev || onNext) && (
            <div className="flex gap-1">
              <button
                onClick={onPrev}
                disabled={!onPrev}
                aria-label="Previous"
                className="rounded-md border border-slate-300 px-2 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-40"
              >
                ↑
              </button>
              <button
                onClick={onNext}
                disabled={!onNext}
                aria-label="Next"
                className="rounded-md border border-slate-300 px-2 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-40"
              >
                ↓
              </button>
            </div>
          )}
          <button onClick={onClose} aria-label="Close" className="rounded-md border border-slate-300 px-2 py-1 text-sm">
            ✕
          </button>
        </div>
        {tabs && activeTab !== undefined && onTabChange && (
          <div className="flex-none px-4">
            <Tabs tabs={tabs} active={activeTab} onChange={onTabChange} />
          </div>
        )}
        <div className="flex-1 overflow-y-auto overscroll-contain p-4">{children}</div>
        {footer && (
          <div className="flex-none border-t border-slate-200 bg-white p-3">{footer}</div>
        )}
      </div>
    </div>
  );
}
