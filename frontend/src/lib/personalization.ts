// Etsy returns buyer-entered personalization HTML-escaped ("Pippa&#39;s Library"), and
// file-upload answers as bare image URLs. Both are stored verbatim in
// order_lines.variation_text, so we tidy them up for display here.

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

export function decodeHtmlEntities(text: string): string {
  return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|([a-z]+));/gi, (match, dec, hex, name) => {
    if (dec || hex) {
      const code = dec ? parseInt(dec, 10) : parseInt(hex, 16);
      try {
        return String.fromCodePoint(code);
      } catch {
        return match;
      }
    }
    return NAMED_ENTITIES[String(name).toLowerCase()] ?? match;
  });
}

export type PersonalizationPart = { kind: "text"; text: string } | { kind: "url"; url: string; index: number };

const URL_RE = /https?:\/\/[^\s;]+/g;

/** Splits decoded personalization into text and URL parts so URLs can render as a
 *  short placeholder ("file 1") instead of the full link. */
export function splitPersonalization(raw: string): PersonalizationPart[] {
  const text = decodeHtmlEntities(raw);
  const parts: PersonalizationPart[] = [];
  let last = 0;
  let index = 0;
  for (const m of text.matchAll(URL_RE)) {
    const start = m.index ?? 0;
    if (start > last) parts.push({ kind: "text", text: text.slice(last, start) });
    index += 1;
    parts.push({ kind: "url", url: m[0], index });
    last = start + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}
