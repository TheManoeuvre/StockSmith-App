import { createRootRoute, Link, Outlet } from "@tanstack/react-router";
import { SyncStatusIndicator } from "../components/common/SyncStatusIndicator";
import { UnsavedChangesDialog } from "../components/common/UnsavedChangesDialog";
import { DirtyRegistryProvider } from "../hooks/useDirtyRegistry";
import { GuardProvider, useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { useTauriCloseGuard } from "../hooks/useTauriCloseGuard";

export const Route = createRootRoute({
  component: RootLayout,
});

/**
 * The unsaved-changes registry lives at the root, not on any one page.
 *
 * Two things need it to be app-wide: the router blocker has to fire when you navigate from
 * one page to another (not just between tabs of the same one), and the desktop window-close
 * prompt has to know about unsaved work wherever it is. Individual editors register
 * themselves wherever they happen to be mounted; pages only reach for useGuard() when they
 * have their own destructive controls to veto.
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
  // Tauri never fires beforeunload for a window close, so the desktop app needs its own
  // hook into the same dialog. `destroy` is what actually closes the window on discard.
  useTauriCloseGuard((destroy) => guard.attempt(destroy));
  return (
    <GuardProvider guard={guard}>
      <RootChrome />
      <UnsavedChangesDialog {...guard.dialogProps} />
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
