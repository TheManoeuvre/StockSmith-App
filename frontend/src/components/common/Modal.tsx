import { useEffect, type ReactNode } from "react";

/**
 * Bare modal shell — backdrop, centred card, Escape to dismiss.
 *
 * Four hand-rolled copies of this backdrop already exist (CancelOrderDialog,
 * BulkBomAmendModal, ListingPickerModal, EtsyListingPickerModal). They're deliberately left
 * alone: each has its own sizing and internal scroll structure, and retrofitting them is a
 * pure refactor with its own risk. They're good candidates for a follow-up.
 */
export function Modal({
  title,
  footer,
  maxWidth = "max-w-md",
  onClose,
  children,
}: {
  title?: string;
  footer?: ReactNode;
  maxWidth?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`flex max-h-[90vh] w-full ${maxWidth} flex-col rounded bg-white shadow-lg`}
      >
        {title && (
          <div className="border-b border-slate-200 p-4">
            <h2 className="text-lg font-semibold">{title}</h2>
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 p-4">{footer}</div>}
      </div>
    </div>
  );
}
