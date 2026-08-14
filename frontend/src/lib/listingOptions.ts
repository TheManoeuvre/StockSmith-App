/**
 * Readable labels for the marketplace option values a listing profile stores.
 *
 * The values are each marketplace's own vocabulary and are sent verbatim — they change on
 * Etsy's and eBay's schedule, not ours, so they are deliberately not modelled as local
 * enums. Only the labels are ours.
 *
 * This exists because a settings form offering "i_did" and "made_to_order" is asking the
 * user to read an API. The stored value never changes; only what the picker shows.
 */

export interface Option {
  value: string;
  label: string;
}

// Etsy's own wording in Seller Manager, so the two agree when someone checks.
export const ETSY_WHO_MADE: Option[] = [
  { value: "i_did", label: "I did" },
  { value: "collective", label: "A member of my shop" },
  { value: "someone_else", label: "Another company or person" },
];

export const ETSY_WHEN_MADE: Option[] = [
  { value: "made_to_order", label: "Made to order" },
  { value: "2020_2026", label: "2020 – 2026" },
  { value: "2010_2019", label: "2010 – 2019" },
  { value: "2007_2009", label: "2007 – 2009" },
  { value: "2000_2006", label: "2000 – 2006" },
  { value: "before_2007", label: "Before 2007" },
  { value: "1990s", label: "1990s" },
  { value: "1980s", label: "1980s" },
  { value: "1970s", label: "1970s" },
  { value: "1960s", label: "1960s" },
  { value: "1950s", label: "1950s" },
  { value: "1940s", label: "1940s" },
  { value: "1930s", label: "1930s" },
  { value: "1920s", label: "1920s" },
  { value: "1910s", label: "1910s" },
  { value: "1900s", label: "1900s" },
  { value: "1800s", label: "1800s" },
  { value: "1700s", label: "1700s" },
  { value: "before_1700", label: "Before 1700" },
];

export const ETSY_IS_SUPPLY: Option[] = [
  { value: "false", label: "A finished product" },
  { value: "true", label: "A supply or tool for making things" },
];

/**
 * eBay item conditions.
 *
 * The values are eBay's condition enum and must be sent exactly; the labels are plain
 * English. Trimmed to what a maker actually sells — eBay also defines several refurbished
 * grades that need authorisation to use, and listing them here would offer choices that
 * get rejected at publish.
 *
 * Note "Seconds" maps to eBay's NEW_WITH_DEFECTS, which is precisely what a second is: new,
 * unused, with a cosmetic flaw. There is no SECONDS value in eBay's vocabulary.
 *
 * UNVERIFIED against a live account, in common with every other eBay surface in this
 * codebase — the values come from documentation, not from a call that succeeded.
 */
export const EBAY_CONDITION: Option[] = [
  { value: "NEW", label: "New" },
  { value: "LIKE_NEW", label: "Like new" },
  { value: "NEW_OTHER", label: "New, without packaging" },
  { value: "NEW_WITH_DEFECTS", label: "Seconds (new, with a flaw)" },
  { value: "USED_EXCELLENT", label: "Used – excellent" },
  { value: "USED_GOOD", label: "Used – working" },
  { value: "USED_ACCEPTABLE", label: "Used – acceptable" },
  { value: "FOR_PARTS_OR_NOT_WORKING", label: "For parts or not working" },
];

/** The label for a stored value, falling back to the raw value if it's one we don't know. */
export function labelFor(options: Option[], value: string | null | undefined): string {
  if (!value) return "";
  return options.find((o) => o.value === value)?.label ?? value;
}
