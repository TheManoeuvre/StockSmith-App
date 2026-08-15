import { createRootRoute, Link, Outlet } from "@tanstack/react-router";
import { SyncStatusIndicator } from "../components/common/SyncStatusIndicator";
import { MaintenanceOverlay } from "../components/common/MaintenanceOverlay";
import { UnsavedChangesDialog } from "../components/common/UnsavedChangesDialog";
import { DirtyRegistryProvider } from "../hooks/useDirtyRegistry";
import { GuardProvider, useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";

export const Route = createRootRoute({
  component: RootLayout,
});

/**
 * The unsaved-changes registry lives at the root, not on any one page.
 *
 * It has to be app-wide because the router blocker fires when you navigate from one page to
 * another, not just between tabs of the same one. Individual editors register themselves
 * wherever they happen to be mounted; pages only reach for useGuard() when they have their
 * own destructive controls to veto.
 */
function RootLayout() {
  return (
    <DirtyRegistryProvider>
      <RootShell />
    </DirtyRegistryProvider>
  );
}

function RootShell() {
  const guard = useUnsavedChangesGuard();
  // No window-close prompt any more, deliberately. Closing the window now hides it to the
  // tray (see src-tauri/lib.rs's on_window_event), so unsaved work isn't going anywhere —
  // the window is still there, with the form still in it. Asking "discard your changes?"
  // for something that discards nothing trains people to click through the dialog that
  // does matter. Quitting from the tray is the only path that can now lose work, and it
  // carries its own confirmation.
  return (
    <GuardProvider guard={guard}>
      <RootChrome />
      <UnsavedChangesDialog {...guard.dialogProps} />
      {/* App-wide for the same reason the guard is: a restore on the host locks out every page,
          not just Settings. */}
      <MaintenanceOverlay />
    </GuardProvider>
  );
}

function RootChrome() {
  const linkClass = "px-3 py-2 rounded hover:bg-slate-200 [&.active]:bg-slate-300 font-medium";
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <nav className="flex gap-1 border-b border-slate-200 bg-white px-4 py-2">
        <Link to="/" className={linkClass}>
          Dashboard
        </Link>
        <Link to="/materials" className={linkClass}>
          Materials
        </Link>
        <Link to="/products" className={linkClass}>
          Products
        </Link>
        <Link to="/purchases" className={linkClass}>
          Purchases
        </Link>
        <Link to="/orders" className={linkClass}>
          Orders
        </Link>
        <Link to="/settings" className={linkClass}>
          Settings
        </Link>
        {/* ml-auto lives on the indicator itself so the nav links stay a plain flex row
            and nothing shifts when it renders nothing (no platform connected yet). */}
        <SyncStatusIndicator />
      </nav>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
