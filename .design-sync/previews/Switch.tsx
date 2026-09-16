import { useState } from "react";
import { Switch } from "stocksmith-ui";

export const States = () => {
  const [on, setOn] = useState(true);
  const [off, setOff] = useState(false);
  return (
    <div className="flex items-center gap-6">
      <Switch checked={on} onChange={setOn} ariaLabel="Enabled" />
      <Switch checked={off} onChange={setOff} ariaLabel="Disabled" />
      <Switch checked disabled onChange={() => {}} ariaLabel="On, locked" />
      <Switch checked={false} disabled onChange={() => {}} ariaLabel="Off, locked" />
    </div>
  );
};

/** As a labelled settings row (Settings > Background sync). */
export const SettingsRow = () => {
  const [checked, setChecked] = useState(true);
  return (
    <label htmlFor="bg-sync" className="flex w-80 items-center justify-between gap-3 rounded-md bg-white p-3 shadow-sm">
      <span>
        <span className="block text-sm font-medium text-slate-900">Sync orders in the background</span>
        <span className="block text-xs text-slate-500">Every 15 minutes while the app is open</span>
      </span>
      <Switch id="bg-sync" checked={checked} onChange={setChecked} />
    </label>
  );
};
