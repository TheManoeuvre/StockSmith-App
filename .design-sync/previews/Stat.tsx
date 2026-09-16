import { Stat } from "stocksmith-ui";

/** The persistent stat row at the top of a Materials detail panel. */
export const MaterialStats = () => (
  <div className="grid grid-cols-3 gap-2 bg-slate-50 p-3">
    <Stat label="On hand" value="1,240 g" sub="counted" />
    <Stat label="On order" value="2,000 g" sub="due 24 Sep" />
    <Stat label="To stockout" value="3.5 wks" sub="Critical" valueClassName="text-red-600" tone="highlight" />
  </div>
);

/** A Products detail row: a zero sellable figure is coloured red. */
export const ProductStats = () => (
  <div className="grid grid-cols-3 gap-2 bg-slate-50 p-3">
    <Stat label="Ready to ship" value="0" sub="assembled on demand" valueClassName="text-red-600" />
    <Stat label="On hand" value="18" sub="4 reserved to orders" />
    <Stat label="Buildable" value="42" sub="from material on hand" />
  </div>
);

export const Highlight = () => (
  <div className="w-48 bg-slate-50 p-3">
    <Stat label="Order value" value="£86.40" sub="after £4.00 discount" tone="highlight" />
  </div>
);
