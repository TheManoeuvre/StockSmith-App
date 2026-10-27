import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GITHUB_RELEASES_URL, GITHUB_REPO_URL, releaseUrl } from "../../lib/appUpdate";
import { parseReleaseNotes, splitBold } from "../../lib/releaseNotes";
import { openExternalUrl } from "../../lib/tauri";
import { canUpdate, installUpdate } from "../../lib/updater";
import { useAppUpdate } from "../../hooks/useAppUpdate";
import { Disclosure } from "./Disclosure";
import { SettingsCard } from "./SettingsCard";

type Notices = Record<string, { name: string; version: string; license: string }[]>;

const PRIMARY_BUTTON = "h-7 rounded-md bg-slate-900 px-2.5 text-xs font-semibold text-white disabled:opacity-50";
const SECONDARY_BUTTON =
  "h-7 rounded-md border border-slate-300 px-2.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50";

function ExternalLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        void openExternalUrl(href);
      }}
      className="text-blue-700 hover:underline"
    >
      {children}
    </a>
  );
}

function ReleaseNotes({ body }: { body: string }) {
  const sections = parseReleaseNotes(body);
  if (sections.length === 0) return null;
  return (
    <div className="flex max-h-72 flex-col gap-2.5 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-700">
      {sections.map((section, i) => (
        <div key={i}>
          {section.heading && (
            <p className="pb-1 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400">
              {section.heading}
            </p>
          )}
          <ul className="flex list-disc flex-col gap-1.5 pl-4">
            {section.items.map((item, j) => (
              <li key={j}>
                {splitBold(item).map((part, k) =>
                  part.bold ? <strong key={k}>{part.text}</strong> : <span key={k}>{part.text}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function UpdateCard() {
  const app = useAppUpdate({ immediate: true });
  const [installing, setInstalling] = useState<{ loaded: number; total: number | null } | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  async function install() {
    setInstallError(null);
    setInstalling({ loaded: 0, total: null });
    try {
      await installUpdate((loaded, total) => setInstalling({ loaded, total }));
    } catch (error) {
      setInstalling(null);
      setInstallError(error instanceof Error ? error.message : String(error));
    }
  }

  const update = app.update;
  const ignored = update !== null && app.ignoredVersion === update.version && !app.available;
  const percent = installing?.total ? Math.min(100, Math.round((installing.loaded / installing.total) * 100)) : null;

  let status: string;
  if (!canUpdate()) status = "Updates are only checked in the desktop app.";
  else if (app.isChecking && !update) status = "Checking for updates…";
  else if (app.checkFailed && !update) status = "Couldn't check for updates. Will try again later.";
  else if (app.available) status = `Version ${update!.version} is available.`;
  else if (ignored) status = `Version ${update!.version} is available, but you've chosen to ignore it.`;
  else status = "StockSmith is up to date.";

  return (
    <SettingsCard
      title="Updates"
      help={status}
      action={
        canUpdate() && !installing ? (
          <button type="button" onClick={app.checkNow} disabled={app.isChecking} className={SECONDARY_BUTTON}>
            {app.isChecking ? "Checking…" : "Check now"}
          </button>
        ) : undefined
      }
    >
      {update && (app.available || ignored) && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <p className="text-[13px] font-medium">
              What's new in {update.version}
              {update.date && (
                <span className="font-normal text-slate-500">
                  {" "}
                  · {new Date(update.date).toLocaleDateString()}
                </span>
              )}
            </p>
            {update.notes ? (
              <ReleaseNotes body={update.notes} />
            ) : (
              <p className="text-[12px] text-slate-500">No release notes were published for this version.</p>
            )}
            <p className="text-[11.5px] text-slate-500">
              These notes cover {update.version} only
              {app.installedVersion ? ` — you're on ${app.installedVersion}` : ""}.{" "}
              <ExternalLink href={GITHUB_RELEASES_URL}>Full release history on GitHub</ExternalLink>
              {" · "}
              <ExternalLink href={releaseUrl(update.version)}>This release on GitHub</ExternalLink>
            </p>
          </div>

          {installing ? (
            <div role="status" className="flex flex-col gap-1">
              <p className="text-[12px] text-slate-600">
                {percent === null || percent < 100
                  ? `Downloading… ${percent !== null ? `${percent}%` : ""}`
                  : "Installing — StockSmith will restart."}
              </p>
              <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
                <div className="h-full bg-blue-600" style={{ width: `${percent ?? 5}%` }} />
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <button type="button" onClick={install} className={PRIMARY_BUTTON}>
                {ignored ? "Install anyway" : "Install and restart"}
              </button>
              {ignored ? (
                <button type="button" onClick={app.unignore} className={SECONDARY_BUTTON}>
                  Stop ignoring
                </button>
              ) : (
                <button type="button" onClick={() => app.ignore(update.version)} className={SECONDARY_BUTTON}>
                  Ignore this version
                </button>
              )}
              {!ignored && (
                <span className="text-[11.5px] text-slate-500">
                  Ignoring hides the notice until a newer version is released.
                </span>
              )}
            </div>
          )}
          {installError && (
            <p role="alert" className="text-[12px] text-red-700">
              The update couldn't be installed: {installError}
            </p>
          )}
        </div>
      )}
    </SettingsCard>
  );
}

function ThirdPartyNotices() {
  // Loaded when first opened: the list runs to several hundred entries and nothing else on
  // the page needs it.
  const [open, setOpen] = useState(false);
  const { data: notices } = useQuery({
    queryKey: ["third-party-notices"],
    queryFn: async () => (await import("../../assets/thirdPartyNotices.json")).default as Notices,
    enabled: open,
    staleTime: Infinity,
  });
  const total = notices ? Object.values(notices).reduce((sum, list) => sum + list.length, 0) : null;

  return (
    <SettingsCard
      title="Third-party software"
      help="StockSmith is built on open-source packages, each under its own licence."
    >
      <div className="rounded-md border border-slate-200">
        <Disclosure
          title="Show licences"
          summary={total !== null ? `${total} packages` : undefined}
          open={open}
          onOpenChange={setOpen}
        >
          {!notices ? (
            <p className="text-[12px] text-slate-500">Loading…</p>
          ) : (
            <div className="flex max-h-96 flex-col gap-3 overflow-auto pr-2">
              {Object.entries(notices).map(([group, packages]) => (
                <div key={group}>
                  <p className="pb-1 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400">
                    {group} · {packages.length}
                  </p>
                  <table className="w-full text-[11.5px]">
                    <tbody>
                      {packages.map((p) => (
                        <tr key={`${p.name}@${p.version}`} className="border-b border-slate-100 last:border-0">
                          <td className="py-0.5 pr-3 font-medium text-slate-700">{p.name}</td>
                          <td className="py-0.5 pr-3 tabular-nums text-slate-500">{p.version}</td>
                          <td className="py-0.5 text-slate-600">{p.license}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}
        </Disclosure>
      </div>
    </SettingsCard>
  );
}

export function AboutSettings() {
  const { installedVersion } = useAppUpdate();
  return (
    <div className="flex flex-col gap-4">
      <SettingsCard title="About StockSmith" help="Stock, orders and listings for a small maker business.">
        <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 text-[12.5px]">
          <dt className="text-slate-500">Version</dt>
          <dd className="font-medium tabular-nums">{installedVersion ?? "Development build"}</dd>
          <dt className="text-slate-500">Source</dt>
          <dd>
            <ExternalLink href={GITHUB_REPO_URL}>github.com/TheManoeuvre/StockSmith-App</ExternalLink>
          </dd>
          <dt className="text-slate-500">Licence</dt>
          <dd className="text-slate-700">
            Copyright © 2026 Benjamin Hinett. All rights reserved. The source is available for viewing only;
            using, copying or distributing it needs written permission.{" "}
            <ExternalLink href={`${GITHUB_REPO_URL}/blob/main/LICENSE`}>Full licence</ExternalLink>
          </dd>
        </dl>
      </SettingsCard>
      <UpdateCard />
      <ThirdPartyNotices />
    </div>
  );
}
