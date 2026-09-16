import { useState } from "react";
import { Tabs } from "stocksmith-ui";

/** The pane switcher inside a detail panel (Materials). */
export const DetailPanes = () => {
  const [active, setActive] = useState("details");
  return (
    <div className="w-96">
      <Tabs
        tabs={[
          { id: "details", label: "Details" },
          { id: "counting", label: "Counting" },
          { id: "supplier", label: "Supplier" },
          { id: "history", label: "History" },
        ]}
        active={active}
        onChange={setActive}
      />
      <p className="p-3 text-sm text-slate-600">Active pane: {active}</p>
    </div>
  );
};
