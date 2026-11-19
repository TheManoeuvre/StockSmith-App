/**
 * The in-app help, read from the same markdown files GitHub displays.
 *
 * Source of truth is `docs/help/*.md` at the repository root — written as plain GitHub-flavoured
 * markdown so the folder reads properly on GitHub, and bundled here at build time so the app's
 * help works offline and always matches the installed version. One copy, two places to read it.
 *
 * Conventions the files follow (and help.test.ts enforces):
 * - one file per page, named `<slug>.md`; `README.md` is the index (GitHub shows it for the folder);
 * - the first line is a `# Title`;
 * - links between pages are relative (`connecting-square.md`, `concepts.md#kitting`), which GitHub
 *   resolves natively and `resolveHelpHref` turns into in-app navigation;
 * - every page appears in HELP_SECTIONS, so nothing written is unreachable from the Help sidebar.
 */

const sources = import.meta.glob("../../../docs/help/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Sidebar order. Adding a page means adding its file and its slug here. */
export const HELP_SECTIONS = [
  { title: "Start here", pages: ["getting-started", "concepts"] },
  { title: "Stock", pages: ["material-categories"] },
  { title: "Stores", pages: ["connecting-square", "connecting-etsy", "connecting-ebay"] },
  { title: "Your data", pages: ["backup-and-restore", "troubleshooting"] },
] as const;

export type HelpSlug = (typeof HELP_SECTIONS)[number]["pages"][number] | "index";

export const HELP_SLUGS: HelpSlug[] = ["index", ...HELP_SECTIONS.flatMap((s) => [...s.pages])];

function slugFromPath(path: string): string {
  const file = path.split("/").pop()!.replace(/\.md$/, "");
  return file === "README" ? "index" : file;
}

const pages = new Map<string, string>(Object.entries(sources).map(([path, text]) => [slugFromPath(path), text]));

/** Every slug that has a file — compared against HELP_SLUGS by the tests. */
export function availableHelpSlugs(): string[] {
  return [...pages.keys()];
}

export interface HelpPage {
  slug: string;
  title: string;
  /** The markdown with its leading `# Title` removed; the page renders the title itself. */
  body: string;
}

export function getHelpPage(slug: string): HelpPage | null {
  const text = pages.get(slug);
  if (text === undefined) return null;
  const match = text.match(/^#\s+(.+)\r?\n/);
  return {
    slug,
    title: match ? match[1].trim() : slug,
    body: match ? text.slice(match[0].length) : text,
  };
}

export function isHelpSlug(value: unknown): value is HelpSlug {
  return typeof value === "string" && (HELP_SLUGS as string[]).includes(value);
}

/**
 * GitHub's heading-anchor rule, near enough: lowercase, drop anything that isn't a letter, digit,
 * space or hyphen, spaces to hyphens. Used for the heading ids so `page.md#some-heading` links
 * land in the same place on GitHub and in the app.
 */
export function headingAnchor(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s/g, "-");
}

export type ResolvedHref =
  | { kind: "page"; slug: HelpSlug; anchor?: string }
  | { kind: "anchor"; anchor: string }
  | { kind: "external"; url: string }
  | { kind: "unknown"; href: string };

export function resolveHelpHref(href: string): ResolvedHref {
  if (/^(https?:|mailto:)/i.test(href)) return { kind: "external", url: href };
  if (href.startsWith("#")) return { kind: "anchor", anchor: href.slice(1) };
  const match = href.match(/^(?:\.\/)?([\w-]+)\.md(?:#(.+))?$/);
  if (match) {
    const slug = match[1] === "README" ? "index" : match[1];
    if (isHelpSlug(slug)) return { kind: "page", slug, anchor: match[2] };
  }
  return { kind: "unknown", href };
}

/** Anchors a page's headings produce, for link checking. */
export function headingAnchors(markdown: string): Set<string> {
  const anchors = new Set<string>();
  for (const line of markdown.split(/\r?\n/)) {
    const m = line.match(/^#{1,6}\s+(.+?)\s*$/);
    if (m) anchors.add(headingAnchor(m[1].replace(/[`*_]/g, "")));
  }
  return anchors;
}
