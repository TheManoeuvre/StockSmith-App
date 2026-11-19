import { describe, expect, it } from "vitest";
import {
  availableHelpSlugs,
  getHelpPage,
  headingAnchor,
  headingAnchors,
  HELP_SLUGS,
  PAGE_HELP,
  resolveHelpHref,
} from "./help";

/** Markdown links outside fenced code blocks. */
function linksIn(markdown: string): string[] {
  const withoutCode = markdown.replace(/```[\s\S]*?```/g, "").replace(/`[^`]*`/g, "");
  return [...withoutCode.matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1]);
}

describe("help pages", () => {
  it("has a file for every page in the sidebar, and every file is in the sidebar", () => {
    expect([...availableHelpSlugs()].sort()).toEqual([...HELP_SLUGS].sort());
  });

  it.each(HELP_SLUGS)("%s starts with a # title", (slug) => {
    const page = getHelpPage(slug)!;
    expect(page.title).not.toBe(slug);
    expect(page.title.length).toBeGreaterThan(0);
  });

  it.each(HELP_SLUGS)("every link in %s goes somewhere real", (slug) => {
    const page = getHelpPage(slug)!;
    const broken: string[] = [];
    for (const href of linksIn(page.body)) {
      const target = resolveHelpHref(href);
      if (target.kind === "unknown") broken.push(`${href} (not a help page)`);
      if (target.kind === "anchor" && !headingAnchors(page.body).has(target.anchor)) broken.push(href);
      if (target.kind === "page" && target.anchor) {
        const other = getHelpPage(target.slug)!;
        if (!headingAnchors(other.body).has(target.anchor)) broken.push(href);
      }
    }
    expect(broken).toEqual([]);
  });

  it("the index lists every page, so the folder reads completely on GitHub too", () => {
    const linked = new Set(
      linksIn(getHelpPage("index")!.body)
        .map(resolveHelpHref)
        .filter((t) => t.kind === "page")
        .map((t) => (t as { slug: string }).slug),
    );
    expect(HELP_SLUGS.filter((s) => s !== "index" && !linked.has(s))).toEqual([]);
  });
});

describe("PAGE_HELP", () => {
  it.each(Object.entries(PAGE_HELP))("%s's help link lands on a real heading", (_page, target) => {
    const page = getHelpPage(target.doc);
    expect(page).not.toBeNull();
    if (target.anchor) expect(headingAnchors(page!.body)).toContain(target.anchor);
  });
});

describe("resolveHelpHref", () => {
  it("turns relative .md links into in-app pages", () => {
    expect(resolveHelpHref("concepts.md#kitting")).toEqual({ kind: "page", slug: "concepts", anchor: "kitting" });
    expect(resolveHelpHref("./README.md")).toEqual({ kind: "page", slug: "index", anchor: undefined });
  });

  it("leaves web links external and flags anything else", () => {
    expect(resolveHelpHref("https://example.com")).toEqual({ kind: "external", url: "https://example.com" });
    expect(resolveHelpHref("../plan-onboarding.md").kind).toBe("unknown");
    expect(resolveHelpHref("not-a-page.md").kind).toBe("unknown");
  });
});

describe("headingAnchor", () => {
  it("matches GitHub's anchors", () => {
    expect(headingAnchor("Kitting")).toBe("kitting");
    expect(headingAnchor("1. Get an access token from Square")).toBe("1-get-an-access-token-from-square");
    expect(headingAnchor("Has a material type (groups substitutable materials)")).toBe(
      "has-a-material-type-groups-substitutable-materials",
    );
    expect(headingAnchor("Backups, restoring & moving")).toBe("backups-restoring--moving");
  });
});
