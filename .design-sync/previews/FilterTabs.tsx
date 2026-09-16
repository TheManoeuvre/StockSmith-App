import { useState } from "react";
import { FilterTabs } from "stocksmith-ui";

/** The Materials list's saved views, each with its row count. */
export const WithCounts = () => {
  const [active, setActive] = useState("all");
  return (
    <div className="w-[520px]">
      <FilterTabs
        tabs={[
          { id: "all", label: "All materials", count: 128 },
          { id: "low", label: "Low stock", count: 7 },
          { id: "on-order", label: "On order", count: 3 },
        ]}
        active={active}
        onChange={setActive}
      />
    </div>
  );
};

/** Orders: a status strip where some tabs have no meaningful count. */
export const MixedCounts = () => {
  const [active, setActive] = useState("awaiting");
  return (
    <div className="w-[520px]">
      <FilterTabs
        tabs={[
          { id: "awaiting", label: "Awaiting", count: 12 },
          { id: "blocked", label: "Blocked", count: 2 },
          { id: "shipped", label: "Shipped" },
          { id: "all", label: "All" },
        ]}
        active={active}
        onChange={setActive}
      />
    </div>
  );
};
