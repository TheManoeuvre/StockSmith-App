import { useEffect } from "react";
import { createFileRoute, Link, useLocation } from "@tanstack/react-router";
import { HelpMarkdown } from "../components/help/HelpMarkdown";
import { getHelpPage, HELP_SECTIONS, isHelpSlug, type HelpSlug } from "../lib/help";

/**
 * The in-app help. Pages come from `docs/help/*.md` (see lib/help.ts); the page lives in the URL
 * as `?page=` for the same reason Settings does it — HelpLinks can point straight at one, and the
 * back button works.
 */
export const Route = createFileRoute("/help")({
  validateSearch: (search: Record<string, unknown>): { page?: HelpSlug } =>
    isHelpSlug(search.page) ? { page: search.page } : {},
  component: HelpRoute,
});

function HelpRoute() {
  // Re-checked here as well as in validateSearch: the router keeps unknown search keys it was
  // given, so an old or mistyped ?page= can still arrive. Falling back beats a blank page.
  const { page: requested } = Route.useSearch();
  const slug: HelpSlug = isHelpSlug(requested) ? requested : "index";
  const hash = useLocation({ select: (l) => l.hash });
  const page = getHelpPage(slug);

  // Land on the linked section; otherwise start at the top of the new page.
  useEffect(() => {
    const target = hash ? document.getElementById(hash) : null;
    if (target) target.scrollIntoView();
    else document.querySelector("main")?.scrollTo?.({ top: 0 });
  }, [slug, hash]);

  return (
    <div className="flex gap-8">
      <nav aria-label="Help pages" className="w-48 flex-none max-md:hidden">
        <NavEntry slug="index" current={slug} />
        {HELP_SECTIONS.map((section) => (
          <div key={section.title} className="mt-3">
            <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400">
              {section.title}
            </p>
            {section.pages.map((p) => (
              <NavEntry key={p} slug={p} current={slug} />
            ))}
          </div>
        ))}
      </nav>
      <article className="min-w-0 max-w-3xl flex-1">
        {page ? (
          <>
            <h1 className="mb-1 text-xl font-semibold text-slate-900">{page.title}</h1>
            <HelpMarkdown markdown={page.body} />
          </>
        ) : (
          <p className="text-sm text-slate-500">This help page couldn't be found.</p>
        )}
      </article>
    </div>
  );
}

function NavEntry({ slug, current }: { slug: HelpSlug; current: HelpSlug }) {
  const title = slug === "index" ? "Help home" : (getHelpPage(slug)?.title ?? slug);
  return (
    <Link
      to="/help"
      search={{ page: slug }}
      className={`block rounded-md px-2 py-1 text-[13px] hover:bg-slate-100 ${
        slug === current ? "bg-slate-100 font-medium text-slate-900" : "text-slate-600"
      }`}
    >
      {title}
    </Link>
  );
}
