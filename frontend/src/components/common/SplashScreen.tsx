import { useEffect, useState } from "react";

const SLOW_STARTUP_MESSAGE_DELAY_MS = 6_000;

// Shown while main.tsx waits for the bundled backend to come up (see tryAutoProvisionSettings
// in lib/tauri.ts). The window is visible immediately on launch now, so this is the user's
// only feedback during that gap — without it the app just looks frozen for several seconds.
export function SplashScreen() {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), SLOW_STARTUP_MESSAGE_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50 text-slate-900">
      <h1 className="text-xl font-semibold">StockSmith</h1>
      <div className="h-2 w-64 overflow-hidden rounded-full bg-slate-200">
        <div className="splash-progress-bar h-full rounded-full bg-slate-900" />
      </div>
      <p className="text-sm text-slate-500">
        {slow ? "Still starting up — first launch can take a little longer…" : "Starting up…"}
      </p>
    </div>
  );
}
