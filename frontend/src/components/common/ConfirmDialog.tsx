import { useEffect, useId, useState, type ReactNode } from "react";
import { Modal } from "./Modal";

/**
 * Asks before doing something the user can't easily walk back.
 *
 * Exists because destructive confirmation in this app was `window.confirm` (order deletion,
 * product deactivation) — untestable, unstyled, and with no room to show what is actually about
 * to happen. The footer conventions here are lifted from UnsavedChangesDialog so the two read as
 * the same app: the safe choice is autofocused and on the left, the destructive one is red.
 *
 * `requireTypedText` is for the small number of actions where a misclick is unrecoverable — a
 * whole-database restore, not deleting an unused reference row. Use it sparingly: applied to
 * routine confirmations it just trains people to type without reading.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "danger",
  requireTypedText,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "default";
  /** Exact, case-sensitive phrase the user must type before confirm enables. */
  requireTypedText?: string;
  /** Disables both buttons while the action is in flight, so it can't be fired twice. */
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputId = useId();

  // Reset between openings. Without this, reopening the dialog after a cancel would arrive with
  // the gate already satisfied from last time — which is precisely the deliberate pause the
  // typed confirmation exists to create.
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  if (!open) return null;

  const gateSatisfied = requireTypedText === undefined || typed === requireTypedText;
  const confirmDisabled = busy || !gateSatisfied;

  const confirmClass =
    tone === "danger"
      ? "rounded bg-red-600 px-4 py-2 text-sm text-white disabled:opacity-50 disabled:cursor-not-allowed"
      : "rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <Modal
      title={title}
      // Escape and backdrop clicks must not fire the action, only dismiss — and must do nothing
      // at all mid-flight, or a stray keypress cancels the dialog while the work continues.
      onClose={busy ? () => {} : onCancel}
      footer={
        <>
          <button
            autoFocus
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded border border-slate-300 px-4 py-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {cancelLabel}
          </button>
          <button type="button" disabled={confirmDisabled} onClick={onConfirm} className={confirmClass}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        {body}

        {requireTypedText !== undefined && (
          <label htmlFor={inputId} className="flex flex-col gap-1">
            <span>
              Type <span className="font-mono font-semibold">{requireTypedText}</span> to continue.
            </span>
            <input
              id={inputId}
              value={typed}
              disabled={busy}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setTyped(e.target.value)}
              className="rounded border border-slate-300 px-3 py-2 disabled:bg-slate-50"
            />
          </label>
        )}
      </div>
    </Modal>
  );
}
