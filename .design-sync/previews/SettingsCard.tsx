import { useState } from "react";
import { SaveButton, SettingsCard, Switch } from "stocksmith-ui";

/** Title + help text + a form body — the shape every settings section uses. */
export const WithHelp = () => {
  const [on, setOn] = useState(true);
  return (
    <div className="w-[560px]">
      <SettingsCard
        title="Background syncing"
        help="Closing the window leaves StockSmith running in the notification area, so orders keep importing and stock keeps pushing to Etsy and eBay."
      >
        <div className="flex items-start gap-2 text-sm">
          <Switch id="autostart" className="mt-0.5" checked={on} onChange={setOn} />
          <label htmlFor="autostart" className="text-slate-700">
            Start StockSmith when I sign in
          </label>
        </div>
      </SettingsCard>
    </div>
  );
};

/** A Save button in the header's action slot. */
export const WithAction = () => (
  <div className="w-[560px]">
    <SettingsCard
      title="Currency"
      help="Used for prices, costs and margins across the app."
      action={
        <SaveButton isDirty isPending={false} status="idle">
          Save
        </SaveButton>
      }
    >
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-slate-600">Display currency</span>
        <select className="w-48 rounded border border-slate-300 px-2 py-1 text-sm" defaultValue="GBP">
          <option>GBP</option>
          <option>EUR</option>
          <option>USD</option>
        </select>
      </label>
    </SettingsCard>
  </div>
);

/** Title only, for a short informational card. */
export const TitleOnly = () => (
  <div className="w-[560px]">
    <SettingsCard title="Backup & restore">
      <p className="text-sm text-slate-500">Backups aren't available in the browser build — use the desktop app.</p>
    </SettingsCard>
  </div>
);
