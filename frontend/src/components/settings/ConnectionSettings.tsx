import { useEffect, useState } from "react";
import { getSettings, saveSettings } from "../../lib/tauri";
import { healthCheck } from "../../api/client";
import { useDirtyRegistration } from "../../hooks/useDirtyRegistry";
import { FieldRow } from "../common/FieldRow";
import { SettingsCard } from "./SettingsCard";

/**
 * The Connection page, lifted out of the settings route so it's a `SettingsCard` like every
 * other section. Reads and writes the Tauri store directly (fakeBackend doesn't cover it), and
 * registers its own dirty state so a half-typed backend URL prompts on navigating away — the
 * blocker and dialog are already mounted at the root.
 */
export function ConnectionSettings() {
  const [backendUrl, setBackendUrl] = useState("");
  const [sharedPassword, setSharedPassword] = useState("");
  const [savedBackendUrl, setSavedBackendUrl] = useState("");
  const [savedSharedPassword, setSavedSharedPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [testResult, setTestResult] = useState<"idle" | "ok" | "fail" | "testing">("idle");
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  useEffect(() => {
    getSettings().then((s) => {
      setBackendUrl(s.backendUrl ?? "");
      setSharedPassword(s.sharedPassword ?? "");
      setSavedBackendUrl(s.backendUrl ?? "");
      setSavedSharedPassword(s.sharedPassword ?? "");
      setSettingsLoaded(true);
    });
  }, []);

  const isDirty = backendUrl !== savedBackendUrl || sharedPassword !== savedSharedPassword;
  useDirtyRegistration("connection", "Connection settings", isDirty);

  const handleSave = async () => {
    await saveSettings({ backendUrl, sharedPassword });
    setSavedBackendUrl(backendUrl);
    setSavedSharedPassword(sharedPassword);
  };

  const handleTest = async () => {
    setTestResult("testing");
    const ok = await healthCheck(backendUrl);
    setTestResult(ok ? "ok" : "fail");
  };

  return (
    <SettingsCard title="Connection" help="Where this app finds its backend.">
      <div className="flex max-w-md flex-col gap-4">
        <p className="text-sm text-slate-500">
          {settingsLoaded && backendUrl && sharedPassword ? (
            <>
              Connected to <span className="font-medium text-slate-700">{backendUrl}</span>.
            </>
          ) : (
            "Not connected."
          )}
        </p>

        <FieldRow label="Backend URL">
          <input
            className="w-full rounded border border-slate-300 px-3 py-2 disabled:bg-slate-50 disabled:text-slate-400"
            placeholder="http://homebase.tailnet-name.ts.net:8000"
            value={backendUrl}
            disabled={!settingsLoaded}
            onChange={(e) => setBackendUrl(e.target.value)}
          />
        </FieldRow>

        <label className="flex items-center gap-3">
          <span className="w-36 shrink-0 text-sm text-slate-600">Shared password</span>
          <div className="flex min-w-0 flex-1 gap-2">
            <input
              type={showPassword ? "text" : "password"}
              className="flex-1 rounded border border-slate-300 px-3 py-2 disabled:bg-slate-50 disabled:text-slate-400"
              value={sharedPassword}
              disabled={!settingsLoaded}
              onChange={(e) => setSharedPassword(e.target.value)}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
        </label>

        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={!isDirty}
            className="rounded bg-slate-900 px-4 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save
          </button>
          <button onClick={handleTest} className="rounded border border-slate-300 px-4 py-2">
            Test connection
          </button>
        </div>

        {testResult === "testing" && <p className="text-slate-500">Testing…</p>}
        {testResult === "ok" && <p className="text-green-600">Connected successfully.</p>}
        {testResult === "fail" && <p className="text-red-600">Could not reach the backend.</p>}
      </div>
    </SettingsCard>
  );
}
