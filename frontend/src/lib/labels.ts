import { useCallback, useState } from "react";

/**
 * Location label sizes (docs/plan-locations.md §6). Always stored in millimetres.
 *
 * Two presets and a custom size: 51 x 25 mm, the small thermal label the author uses and the
 * default, and 6 x 4 in, the common shipping-label size. They differ in hierarchy, not just
 * scale (the small one leads with the location's own name, the large one with its full path),
 * so a custom size borrows whichever layout is closer.
 */
export type LabelPreset = "small" | "large" | "custom";

export interface LabelSize {
  preset: LabelPreset;
  widthMm: number;
  heightMm: number;
}

export const SMALL_LABEL: LabelSize = { preset: "small", widthMm: 51, heightMm: 25 };
export const LARGE_LABEL: LabelSize = { preset: "large", widthMm: 152.4, heightMm: 101.6 };

export const LABEL_PRESET_OPTIONS: { value: LabelPreset; label: string }[] = [
  { value: "small", label: "51 × 25 mm" },
  { value: "large", label: "6 × 4 in" },
  { value: "custom", label: "Custom" },
];

/** A custom size must stay printable: big enough to hold a barcode, small enough to be a label. */
export const MIN_LABEL_MM = 20;
export const MAX_LABEL_MM = 300;

export function clampMm(value: number): number {
  if (!Number.isFinite(value)) return MIN_LABEL_MM;
  return Math.min(MAX_LABEL_MM, Math.max(MIN_LABEL_MM, value));
}

export function sizeFor(preset: LabelPreset, current?: LabelSize): LabelSize {
  if (preset === "small") return SMALL_LABEL;
  if (preset === "large") return LARGE_LABEL;
  // Switching to Custom keeps whatever size was showing, so the fields start somewhere sensible.
  return { preset: "custom", widthMm: current?.widthMm ?? SMALL_LABEL.widthMm, heightMm: current?.heightMm ?? SMALL_LABEL.heightMm };
}

/** Which of the two designed layouts a size uses: the roomy one from about 100 mm wide up. */
export function layoutFor(size: LabelSize): "small" | "large" {
  return size.widthMm >= 100 ? "large" : "small";
}

export function describeSize(size: LabelSize): string {
  if (size.preset === "small") return "51 × 25 mm";
  if (size.preset === "large") return "6 × 4 in";
  return `${trim(size.widthMm)} × ${trim(size.heightMm)} mm`;
}

function trim(n: number): string {
  return String(Math.round(n * 10) / 10);
}

const STORAGE_KEY = "stocksmith-label-size";

/** What's on this computer's saved size, or the small default. Per computer on purpose: the
 *  printer is attached to this machine, not to the database. */
export function loadLabelSize(): LabelSize {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return SMALL_LABEL;
    const parsed = JSON.parse(raw) as Partial<LabelSize>;
    if (parsed.preset === "small") return SMALL_LABEL;
    if (parsed.preset === "large") return LARGE_LABEL;
    if (parsed.preset === "custom" && typeof parsed.widthMm === "number" && typeof parsed.heightMm === "number") {
      return { preset: "custom", widthMm: clampMm(parsed.widthMm), heightMm: clampMm(parsed.heightMm) };
    }
  } catch {
    /* unreadable or unavailable storage: fall through to the default */
  }
  return SMALL_LABEL;
}

export function saveLabelSize(size: LabelSize): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(size));
  } catch {
    /* private mode: the choice lasts until the page closes */
  }
}

export function useLabelSize(): [LabelSize, (next: LabelSize) => void] {
  const [size, setSize] = useState<LabelSize>(loadLabelSize);
  const update = useCallback((next: LabelSize) => {
    setSize(next);
    saveLabelSize(next);
  }, []);
  return [size, update];
}

/** Longest ancestor path (in characters) the small label shows before dropping outer segments. */
const SMALL_PATH_BUDGET = 62;
/** Up to this many characters the path fits on one line, so the readable code can stay too. */
const SMALL_ONE_LINE = 26;

/**
 * The ancestor path as printed on the small label. When it won't fit, the outermost ancestors
 * go first (behind a leading "…") so the nearest parent always survives.
 */
export function trimmedPath(ancestors: string[], budget = SMALL_PATH_BUDGET): string {
  const join = (parts: string[]) => parts.join(" › ");
  if (join(ancestors).length <= budget) return join(ancestors);
  let kept = ancestors.slice();
  while (kept.length > 1 && `… › ${join(kept)}`.length > budget) kept = kept.slice(1);
  return `… › ${join(kept)}`;
}

/** Whether the small label has room for the readable code under its barcode. */
export function smallLabelShowsCode(pathText: string): boolean {
  return pathText.length > 0 && pathText.length <= SMALL_ONE_LINE;
}
