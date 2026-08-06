import { Modal } from "./Modal";

/**
 * Asks before doing something that would throw away unsaved edits.
 *
 * Names what's unsaved rather than saying "you have unsaved changes" — on this page several
 * editors can be dirty at once across different tabs, and "Bill of Materials, Kitting BOM"
 * is the difference between a useful warning and an alarming one.
 *
 * There is deliberately no "Save all" button. Each editor posts to its own endpoint with its
 * own validation and its own error surface; saving several at once would need partial-failure
 * handling ("2 of 3 saved, the third was rejected") that doesn't exist anywhere in this app.
 */
export function UnsavedChangesDialog({
  open,
  labels,
  onDiscard,
  onCancel,
}: {
  open: boolean;
  labels: string[];
  onDiscard: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;

  return (
    <Modal
      title="Unsaved changes"
      onClose={onCancel}
      footer={
        <>
          <button
            autoFocus
            onClick={onCancel}
            className="rounded border border-slate-300 px-4 py-2 text-sm"
          >
            Keep editing
          </button>
          <button onClick={onDiscard} className="rounded bg-red-600 px-4 py-2 text-sm text-white">
            Discard changes
          </button>
        </>
      }
    >
      <p className="text-sm">
        {labels.length > 0 ? (
          <>
            You have unsaved changes in{" "}
            <span className="font-medium">{labels.join(", ")}</span>. Leaving now will discard them.
          </>
        ) : (
          "You have unsaved changes. Leaving now will discard them."
        )}
      </p>
    </Modal>
  );
}
