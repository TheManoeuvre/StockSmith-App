import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "@tanstack/react-router";
import { headingAnchor, resolveHelpHref } from "../../lib/help";
import { openExternalUrl } from "../../lib/tauri";

/** Plain text of a heading's children, for its anchor id. */
function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number") return String(child);
      if (isValidElement<{ children?: ReactNode }>(child)) return textOf(child.props.children);
      return "";
    })
    .join("");
}

function heading(Tag: "h2" | "h3" | "h4", className: string): Components["h2"] {
  return ({ children }) => (
    <Tag id={headingAnchor(textOf(children))} className={`scroll-mt-4 ${className}`}>
      {children}
    </Tag>
  );
}

const linkClass = "text-slate-900 underline decoration-slate-400 underline-offset-2 hover:decoration-slate-900";

const components: Components = {
  // The page renders its own title, so a stray H1 in the body is demoted rather than doubled up.
  h1: heading("h2", "mt-6 mb-2 text-lg font-semibold text-slate-900"),
  h2: heading("h2", "mt-6 mb-2 text-lg font-semibold text-slate-900"),
  h3: heading("h3", "mt-5 mb-1.5 text-base font-semibold text-slate-900"),
  h4: heading("h4", "mt-4 mb-1 text-sm font-semibold text-slate-900"),
  p: ({ children }) => <p className="my-2.5 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="my-2.5 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2.5 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 rounded-md border-l-4 border-amber-400 bg-amber-50 px-3 py-1 text-slate-700">
      {children}
    </blockquote>
  ),
  code: ({ children }) => <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.9em]">{children}</code>,
  pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-md bg-slate-100 p-3 text-sm">{children}</pre>,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border-b border-slate-300 px-2 py-1.5 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b border-slate-200 px-2 py-1.5 align-top">{children}</td>,
  hr: () => <hr className="my-5 border-slate-200" />,
  a: ({ href = "", children }) => {
    const target = resolveHelpHref(href);
    if (target.kind === "page") {
      return (
        <Link to="/help" search={{ page: target.slug }} hash={target.anchor} className={linkClass}>
          {children}
        </Link>
      );
    }
    if (target.kind === "anchor") {
      return (
        <a
          href={href}
          className={linkClass}
          onClick={(e) => {
            e.preventDefault();
            document.getElementById(target.anchor)?.scrollIntoView();
          }}
        >
          {children}
        </a>
      );
    }
    if (target.kind === "external") {
      return (
        <a
          href={target.url}
          className={linkClass}
          onClick={(e) => {
            e.preventDefault();
            void openExternalUrl(target.url);
          }}
        >
          {children}
        </a>
      );
    }
    // Unresolvable — help.test.ts fails the build on these, so this only shows mid-edit.
    return <span>{children}</span>;
  },
};

export function HelpMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="text-sm text-slate-700">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
