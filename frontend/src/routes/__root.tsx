import { createRootRoute, Link, Outlet } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
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
      </nav>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
