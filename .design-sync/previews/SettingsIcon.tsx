import { SettingsIcon } from "stocksmith-ui";

/** As it sits in the sidebar nav row (see routes/__root.tsx). */
export const NavRow = () => (
  <div className="w-44 rounded-md bg-slate-100 px-[9px] py-[7px] text-[13px] font-medium text-slate-900">
    <span className="flex items-center gap-2"><SettingsIcon /><span>Settings</span></span>
  </div>
);

export const Glyph = () => <span className="inline-flex text-slate-700"><SettingsIcon /></span>;
