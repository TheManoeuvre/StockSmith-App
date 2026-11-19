import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FakeRoute } from "../test/fakeBackend";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
  openExternalUrl: vi.fn(),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

const WAIT = { timeout: 5000 };

function renderAt(url: string, routes: FakeRoute[]) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    ...routes,
    { method: "GET", path: /^\/orders\?/, respond: () => ({ items: [], total: 0, limit: 1, offset: 0 }) },
    { method: "GET", path: /^\/products\?/, respond: () => ({ items: [], total: 0, limit: 50, offset: 0 }) },
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => setRoutes([]));

describe("first-run empty states", () => {
  it("Materials says what to do when there are none", async () => {
    renderAt("/materials", []);
    expect(await screen.findByRole("heading", { name: "No materials yet" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How to add materials" })).toBeInTheDocument();
  });

  it("Products says what to do when there are none", async () => {
    renderAt("/products", []);
    expect(await screen.findByRole("heading", { name: "No products yet" }, WAIT)).toBeInTheDocument();
  });

  it("Purchases says what to do when there are none", async () => {
    renderAt("/purchases", []);
    expect(await screen.findByRole("heading", { name: "No purchases yet" }, WAIT)).toBeInTheDocument();
  });

  it("Orders explains where orders come from when there are none at all", async () => {
    renderAt("/orders", []);
    expect(await screen.findByRole("heading", { name: "No orders yet" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a store" })).toBeInTheDocument();
  });

  it("Orders doesn't show it once any order exists, even if none are awaiting", async () => {
    renderAt("/orders", [
      {
        method: "GET",
        path: /^\/orders\?.*status_filter=shipped/,
        respond: () => ({ items: [], total: 4, limit: 1, offset: 0 }),
      },
    ]);
    expect(await screen.findByRole("heading", { level: 1, name: "Orders" }, WAIT)).toBeInTheDocument();
    // Let the per-tab counts settle before asserting absence.
    await screen.findAllByText(/\b4\b/, undefined, WAIT);
    expect(screen.queryByRole("heading", { name: "No orders yet" })).not.toBeInTheDocument();
  });

  it("each page title has a help button", async () => {
    renderAt("/materials", []);
    expect(await screen.findByRole("link", { name: /^Help: How StockSmith thinks/ }, WAIT)).toHaveAttribute(
      "href",
      "/help?page=concepts#materials-and-products",
    );
  });
});
