export interface ReleaseNotesSection {
  heading: string | null;
  items: string[];
}

/**
 * Splits a release body into the shape CHANGELOG.md writes it in: `### Heading` sections of
 * `- **Headline.** wrapped detail` bullets. Wrapped continuation lines are joined onto the
 * bullet above, so each item is one paragraph. Inline `**bold**` is left for the caller to
 * render; this stays plain strings so nothing here can inject markup.
 */
export function parseReleaseNotes(body: string): ReleaseNotesSection[] {
  const sections: ReleaseNotesSection[] = [];
  let current: ReleaseNotesSection | null = null;
  const open = (heading: string | null) => {
    current = { heading, items: [] };
    sections.push(current);
  };

  for (const raw of body.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      open(heading[1].trim());
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      if (!current) open(null);
      current!.items.push(line.slice(2).trim());
    } else {
      if (!current) open(null);
      const items: string[] = current!.items;
      if (items.length > 0) items[items.length - 1] += ` ${line}`;
      else items.push(line);
    }
  }
  return sections.filter((s) => s.items.length > 0);
}

/** Splits `**bold**` runs out of a line so they can be rendered as <strong>. */
export function splitBold(text: string): { text: string; bold: boolean }[] {
  return text
    .split(/(\*\*[^*]+\*\*)/)
    .filter(Boolean)
    .map((part) =>
      part.startsWith("**") && part.endsWith("**") && part.length > 4
        ? { text: part.slice(2, -2), bold: true }
        : { text: part, bold: false }
    );
}
