import { useState } from "react";
import type React from "react";
import { Badge, DetailPanel, FieldRow, Stat } from "stocksmith-ui";

// Overlays are position:fixed; the preview card wraps each story in a transformed box that
// becomes their containing block, so the story needs a height of its own to centre inside.
const Frame = ({ height, children }: { height: number; children: React.ReactNode }) => (
  <div style={{ position: "relative", height }}>{children}</div>
);

/** The Materials slide-over: header badge, prev/next, tabs, stat row, footer. */
export const Material = () => {
  const [tab, setTab] = useState("details");
  return (
    <Frame height={600}>
      <DetailPanel
        title="PLA filament - matte black"
        onClose={() => {}}
        onPrev={() => {}}
        onNext={() => {}}
        headerExtra={<Badge className="bg-amber-100 text-amber-800">Warning</Badge>}
        tabs={[
          { id: "details", label: "Details" },
          { id: "counting", label: "Counting" },
          { id: "supplier", label: "Supplier" },
        ]}
        activeTab={tab}
        onTabChange={setTab}
        footer={
          <div className="flex justify-end gap-2">
            <button className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">Revert</button>
            <button className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">Save</button>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="On hand" value="1,240 g" sub="counted" />
            <Stat label="On order" value="2,000 g" sub="due 24 Sep" />
            <Stat label="To stockout" value="3.5 wks" sub="Warning" valueClassName="text-amber-700" />
          </div>
          <FieldRow label="Category">
            <p className="text-sm text-slate-600">Filament</p>
          </FieldRow>
          <FieldRow label="Reorder at">
            <input type="number" defaultValue={500} className="w-28 rounded border border-slate-300 px-2 py-1 text-sm" />
          </FieldRow>
          <FieldRow label="Supplier">
            <p className="text-sm text-slate-600">Filamentive</p>
          </FieldRow>
        </div>
      </DetailPanel>
    </Frame>
  );
};

/** Minimal: title + close only, no tabs or navigation. */
export const Minimal = () => (
  <Frame height={600}>
    <DetailPanel title="Stock take #14" onClose={() => {}}>
      <p className="text-sm text-slate-600">Started 12 Sep - 38 of 61 lines counted</p>
    </DetailPanel>
  </Frame>
);
