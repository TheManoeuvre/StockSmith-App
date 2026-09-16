import { useState } from "react";
import { SettingsNav } from "stocksmith-ui";

const GROUPS = [
  {
    label: "Selling",
    items: [
      { id: "stores-sync", label: "Stores & sync" },
      { id: "pricing-fees", label: "Pricing & fees" },
      { id: "shipping-packaging", label: "Shipping & packaging" },
    ],
  },
  {
    label: "Stock",
    items: [
      { id: "forecasting", label: "Forecasting" },
      { id: "stock-counts", label: "Stock counts" },
      { id: "lists", label: "Lists" },
    ],
  },
  {
    label: "App",
    items: [
      { id: "backup-restore", label: "Backup & restore" },
      { id: "notifications", label: "Notifications" },
      { id: "connection", label: "Connection" },
    ],
  },
];

/** The Settings page's grouped sub-nav, exactly as the app configures it. */
export const SettingsPages = () => {
  const [active, setActive] = useState("stores-sync");
  return <SettingsNav groups={GROUPS} active={active} onChange={setActive} />;
};

/** A single group — the nav is happy with fewer sections. */
export const SingleGroup = () => {
  const [active, setActive] = useState("general");
  return (
    <SettingsNav
      groups={[
        {
          label: "Workspace",
          items: [
            { id: "general", label: "General" },
            { id: "members", label: "Members" },
            { id: "billing", label: "Billing" },
          ],
        },
      ]}
      active={active}
      onChange={setActive}
    />
  );
};
