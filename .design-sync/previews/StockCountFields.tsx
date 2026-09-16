import { useState } from "react";
import { StockCountFields } from "stocksmith-ui";

const classification = {
  abc_class: "B" as const,
  interval_days: 30,
  class_source: "group" as const,
  interval_source: "tier" as const,
  last_stock_take_at: "2026-08-20T09:00:00Z",
  next_due_at: "2026-09-19T09:00:00Z",
  days_overdue: 0,
  is_due: false,
};

/** The Materials Counting tab: labelled rows, value inherited from the category. */
export const Rows = () => {
  const [abc, setAbc] = useState<"A" | "B" | "C" | null>(null);
  const [days, setDays] = useState("");
  return (
    <div className="w-[520px]">
      <StockCountFields
        layout="rows"
        abcClass={abc}
        intervalDays={days}
        classification={classification}
        groupLabel="the Filament category"
        onAbcClassChange={setAbc}
        onIntervalDaysChange={setDays}
        openTake={{ id: 14, status: "pending" }}
      />
    </div>
  );
};

/** Overdue item with an explicit tier override. */
export const RowsOverdue = () => {
  const [abc, setAbc] = useState<"A" | "B" | "C" | null>("A");
  const [days, setDays] = useState("7");
  return (
    <div className="w-[520px]">
      <StockCountFields
        layout="rows"
        abcClass={abc}
        intervalDays={days}
        classification={{ ...classification, abc_class: "A", interval_days: 7, class_source: "item", interval_source: "item", days_overdue: 4, is_due: true }}
        groupLabel={null}
        onAbcClassChange={setAbc}
        onIntervalDaysChange={setDays}
        openTake={null}
      />
    </div>
  );
};

/** The compact inline block on the product Details form. */
export const Inline = () => {
  const [abc, setAbc] = useState<"A" | "B" | "C" | null>(null);
  const [days, setDays] = useState("");
  return (
    <div className="w-[520px]">
      <StockCountFields
        abcClass={abc}
        intervalDays={days}
        classification={{ ...classification, days_overdue: null, is_due: true, last_stock_take_at: null, next_due_at: null }}
        groupLabel="the Coaster type"
        onAbcClassChange={setAbc}
        onIntervalDaysChange={setDays}
      />
    </div>
  );
};
