import type { ReactNode } from "react";
import { SaveIndicator } from "./SaveIndicator";
import type { SaveStatus } from "../../hooks/useSaveStatus";

/**
 * A Save button that is greyed out when there is nothing to save, plus its SaveIndicator.
 *
 * Being disabled IS the signal that everything is saved — before this, every Save button on
 * the product page was permanently enabled, so clicking one told you nothing. Styling follows
 * the existing precedent in routes/settings.tsx.
 *
 * `enabledWhen` overrides the dirty check for command forms (record a build, import a URL),
 * whose buttons are actions rather than saves: StockSection's build form ships with qty 1
 * pre-filled precisely so recording one build is a single click, and gating that on "you
 * changed something" would break it. Those forms still report dirty to the registry, so
 * navigating away with half-typed input is still caught — the guard and the button just ask
 * different questions.
 */
export function SaveButton({
  isDirty,
  isPending,
  status,
  enabledWhen,
  onClick,
  type = "button",
  className = "rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50",
  children,
}: {
  isDirty: boolean;
  isPending: boolean;
  status: SaveStatus;
  enabledWhen?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
  children: ReactNode;
}) {
  const enabled = (enabledWhen ?? isDirty) && !isPending;
  return (
    <>
      <button type={type} onClick={onClick} disabled={!enabled} className={className}>
        {children}
      </button>
      <SaveIndicator status={status} />
    </>
  );
}
