import { useState } from "react";
import {
  LABEL_PRESET_OPTIONS,
  MAX_LABEL_MM,
  MIN_LABEL_MM,
  clampMm,
  sizeFor,
  type LabelPreset,
  type LabelSize,
} from "../../lib/labels";
import { SegmentedControl } from "../common/SegmentedControl";

/**
 * Pick a label size: the two presets, or a custom width and height in millimetres. Used on the
 * print screen and under Settings > Labels, so both change the same saved choice.
 */
export function LabelSizeControl({
  size,
  onChange,
  hint = "Your choice is remembered on this computer.",
}: {
  size: LabelSize;
  onChange: (next: LabelSize) => void;
  hint?: string;
}) {
  // Typed text is kept as typed while editing, and only clamped into range once it's committed,
  // so a half-typed "5" on the way to "51" is never rewritten under the user's cursor.
  const [draft, setDraft] = useState<{ w: string; h: string } | null>(null);
  const w = draft?.w ?? String(size.widthMm);
  const h = draft?.h ?? String(size.heightMm);
  const commit = () => {
    if (!draft) return;
    onChange({ preset: "custom", widthMm: clampMm(Number(draft.w)), heightMm: clampMm(Number(draft.h)) });
    setDraft(null);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-xs font-medium text-slate-700">Label size</div>
      <SegmentedControl<LabelPreset>
        ariaLabel="Label size"
        options={LABEL_PRESET_OPTIONS}
        value={size.preset}
        onChange={(preset) => {
          setDraft(null);
          onChange(sizeFor(preset, size));
        }}
      />
      {size.preset === "custom" && (
        <div className="flex items-center gap-2 text-xs text-slate-600">
          <label className="flex items-center gap-1">
            Width
            <input
              type="number"
              min={MIN_LABEL_MM}
              max={MAX_LABEL_MM}
              value={w}
              onChange={(e) => setDraft({ w: e.target.value, h })}
              onBlur={commit}
              className="w-16 rounded border border-slate-300 px-1.5 py-1 text-right tabular-nums"
            />
          </label>
          ×
          <label className="flex items-center gap-1">
            Height
            <input
              type="number"
              min={MIN_LABEL_MM}
              max={MAX_LABEL_MM}
              value={h}
              onChange={(e) => setDraft({ w, h: e.target.value })}
              onBlur={commit}
              className="w-16 rounded border border-slate-300 px-1.5 py-1 text-right tabular-nums"
            />
          </label>
          mm
        </div>
      )}
      <p className="text-[11.5px] text-slate-500">{hint}</p>
    </div>
  );
}
