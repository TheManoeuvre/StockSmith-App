import { useState, type ReactNode } from "react";

/**
 * One collapsible row inside a settings card: chevron, name, a one-line summary that stays
 * visible while closed, and the body underneath when open. The design canvas's "set-up"
 * panels are built from these, and this replaces the four different show/hide idioms the
 * stores page had grown (an underlined link, a full-width Show/Hide row, a "Show N issue(s)"
 * button and native <details>).
 *
 * Controlled when `open`/`onOpenChange` are given — a caller that needs to veto the toggle
 * (an unsaved form inside) or lazy-load on open does that; otherwise it keeps its own state.
 */
export function Disclosure({
  title,
  summary,
  tone = "normal",
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  children,
}: {
  title: string;
  /** Shown beside the title, truncated to one line. Carries the "at rest" state of the row. */
  summary?: ReactNode;
  /** Colours the summary so a problem reads before the row is opened. */
  tone?: "normal" | "warning" | "danger";
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  const toggle = () => {
    if (onOpenChange) onOpenChange(!open);
    if (controlledOpen === undefined) setUncontrolledOpen(!open);
  };
  const summaryTone =
    tone === "danger" ? "text-red-700" : tone === "warning" ? "text-amber-700" : "text-slate-500";

  return (
    <div className="border-b border-slate-100 last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className={`flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-slate-50 ${open ? "bg-slate-50" : ""}`}
      >
        <span className="w-2.5 text-[11px] text-slate-400" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className="text-[12.5px] font-medium">{title}</span>
        {summary && <span className={`min-w-0 flex-1 truncate text-[11.5px] ${summaryTone}`}>{summary}</span>}
      </button>
      {open && <div className="border-t border-slate-100 bg-[#fcfdff] px-3.5 py-3 pl-[34px]">{children}</div>}
    </div>
  );
}
