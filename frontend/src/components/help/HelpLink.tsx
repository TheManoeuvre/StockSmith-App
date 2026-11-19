import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { getHelpPage, type HelpSlug } from "../../lib/help";

/**
 * A link into the in-app help. `doc` is typed against the help manifest, so a link to a page that
 * doesn't exist fails the type-check rather than shipping as a dead link.
 *
 * With no children it renders the small round "?" used in page headers; with children it is an
 * ordinary inline text link, for empty states and form hints.
 */
export function HelpLink({ doc, anchor, children }: { doc: HelpSlug; anchor?: string; children?: ReactNode }) {
  const title = getHelpPage(doc)?.title ?? "Help";
  if (children) {
    return (
      <Link
        to="/help"
        search={{ page: doc }}
        hash={anchor}
        className="text-slate-900 underline decoration-slate-400 underline-offset-2 hover:decoration-slate-900"
      >
        {children}
      </Link>
    );
  }
  return (
    <Link
      to="/help"
      search={{ page: doc }}
      hash={anchor}
      aria-label={`Help: ${title}`}
      title={`Help: ${title}`}
      className="inline-flex h-5 w-5 flex-none items-center justify-center rounded-full border border-slate-300 text-[11px] font-semibold text-slate-500 hover:border-slate-500 hover:text-slate-800"
    >
      ?
    </Link>
  );
}
