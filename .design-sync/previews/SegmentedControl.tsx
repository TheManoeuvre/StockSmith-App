import { useState } from "react";
import { SegmentedControl } from "stocksmith-ui";

/** Dashboard KPI range toggle. */
export const Range = () => {
  const [value, setValue] = useState("30d");
  return (
    <SegmentedControl
      ariaLabel="Range"
      options={[
        { value: "7d", label: "7 days" },
        { value: "30d", label: "30 days" },
        { value: "90d", label: "90 days" },
      ]}
      value={value}
      onChange={setValue}
    />
  );
};

/** Two-way choice, e.g. a materials unit. */
export const TwoOptions = () => {
  const [value, setValue] = useState("grams");
  return (
    <SegmentedControl
      ariaLabel="Unit"
      options={[
        { value: "grams", label: "Grams" },
        { value: "each", label: "Each" },
      ]}
      value={value}
      onChange={setValue}
    />
  );
};
